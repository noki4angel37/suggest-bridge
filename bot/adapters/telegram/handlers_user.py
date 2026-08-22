"""Private-chat flow: draft → anonymity → moderation queue."""

from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.adapters.telegram import keyboards
from bot.adapters.telegram import media as media_utils
from bot.adapters.telegram.cards import media_summary
from bot.adapters.telegram.deps import TelegramServices
from bot.adapters.telegram.keyboards import (
    DRAFT_ANON,
    DRAFT_CANCEL,
    DRAFT_EDIT,
    DRAFT_NAMED,
    DRAFT_SUBMIT,
    DraftCallback,
)
from bot.adapters.telegram.states import DraftFlow
from bot.core import (
    TEXT_LIMIT,
    MediaItem,
    Platform,
    Source,
    Submission,
    SubmissionStatus,
)

logger = logging.getLogger(__name__)

START_TEXT = (
    "Привет! Это предложка канала.\n\n"
    f"Пришлите текст (до {TEXT_LIMIT} символов), фото, видео, стикер, ссылку "
    "или альбом — я соберу черновик.\n"
    "Затем выберите «Анонимно» или «С моим именем» и нажмите "
    "«Отправить на модерацию».\n\n"
    "Пока черновик не отправлен, текст можно изменить."
)
ADMIN_HINT = (
    "\n\n🛠 Вы администратор: заявки приходят сюда с кнопками модерации, "
    "а ваши посты публикуются без хэштега."
)
BLOCKED_TEXT = "Вы не можете отправлять заявки."
DRAFT_LOST_TEXT = "Черновик не найден — отправьте заявку заново."
NOT_YOURS_TEXT = "Это не ваша заявка."
ALREADY_SENT_TEXT = "Заявка уже отправлена на модерацию."
UNSUPPORTED_TEXT = (
    "Поддерживаются текст, ссылки, фото, видео, стикеры и альбомы."
)

DRAFT_STATUSES = (SubmissionStatus.draft, SubmissionStatus.awaiting_privacy)


def privacy_label(submission: Submission) -> str:
    if submission.want_anonymous is True:
        return "🕶 анонимно"
    if submission.want_anonymous is False:
        return "👤 с моим именем"
    return "не выбрано"


def draft_body(submission: Submission) -> str:
    lines = [f"📝 <b>Черновик #{submission.id}</b>", ""]
    text = (submission.text or "").strip()
    lines.append(escape(text) if text else "<i>Без текста</i>")
    summary = media_summary(submission)
    if summary:
        lines.extend(["", summary])
    lines.extend(["", f"Публикация: <b>{privacy_label(submission)}</b>"])
    return "\n".join(lines)


async def _show_draft(message: Message, submission: Submission) -> None:
    await message.answer(
        draft_body(submission),
        parse_mode="HTML",
        reply_markup=keyboards.draft_keyboard(
            submission.id or 0, want_anonymous=submission.want_anonymous
        ),
    )


async def _refresh_draft(callback: CallbackQuery, submission: Submission) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(
            draft_body(submission),
            parse_mode="HTML",
            reply_markup=keyboards.draft_keyboard(
                submission.id or 0, want_anonymous=submission.want_anonymous
            ),
        )
    except Exception:  # noqa: BLE001 - card may be unchanged or too old
        logger.debug("Черновик %s не перерисован", submission.id)


async def _create_draft(
    message: Message,
    services: TelegramServices,
    state: FSMContext,
    *,
    text: str | None,
    items: list[MediaItem],
) -> None:
    user = message.from_user
    if user is None:
        return
    if services.is_blocked(user.id):
        await message.answer(BLOCKED_TEXT)
        return

    media = [*items, *media_utils.link_items(text, start_index=len(items))]
    try:
        draft = await services.submissions.create_draft(
            source=Source.telegram,
            author_platform_user_id=str(user.id),
            author_display_name=user.full_name,
            author_username=user.username,
            text=text,
            media=media,
            is_admin_post=services.is_admin(user.id),
            source_chat_id=str(message.chat.id),
            source_message_id=str(message.message_id),
        )
    except ValueError:
        await message.answer(
            f"Текст длиннее {TEXT_LIMIT} символов — сократите и пришлите снова."
        )
        return

    await state.set_state(None)
    await state.update_data(draft_id=draft.id)
    await _show_draft(message, draft)


async def _load_draft(
    callback: CallbackQuery, services: TelegramServices, submission_id: int
) -> Submission | None:
    submission = services.submissions.get(submission_id)
    if submission is None:
        await callback.answer(DRAFT_LOST_TEXT, show_alert=True)
        return None
    user = callback.from_user
    if user is None or submission.author_platform_user_id != str(user.id):
        await callback.answer(NOT_YOURS_TEXT, show_alert=True)
        return None
    if submission.status not in DRAFT_STATUSES:
        await callback.answer(ALREADY_SENT_TEXT, show_alert=True)
        return None
    return submission


# --- messages ----------------------------------------------------------------


async def cmd_start(
    message: Message, services: TelegramServices, state: FSMContext
) -> None:
    await state.set_state(None)
    user = message.from_user
    text = START_TEXT
    if user is not None and services.is_admin(user.id):
        text += ADMIN_HINT
    await message.answer(text)


async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.set_state(None)
    await message.answer("Редактирование отменено.")


async def edit_draft_text(
    message: Message, services: TelegramServices, state: FSMContext
) -> None:
    data = await state.get_data()
    submission_id = data.get("editing_id") or data.get("draft_id")
    if submission_id is None:
        await state.set_state(None)
        await message.answer(DRAFT_LOST_TEXT)
        return

    submission = services.submissions.get(int(submission_id))
    if submission is None or submission.status not in DRAFT_STATUSES:
        await state.set_state(None)
        await message.answer(ALREADY_SENT_TEXT)
        return

    try:
        updated = await services.submissions.update_draft(
            int(submission_id), text=message.text
        )
    except ValueError:
        await message.answer(
            f"Текст длиннее {TEXT_LIMIT} символов — сократите и пришлите снова."
        )
        return

    await state.set_state(None)
    await message.answer("Текст обновлён.")
    await _show_draft(message, updated)


async def handle_album(
    message: Message, services: TelegramServices, state: FSMContext
) -> None:
    key = media_utils.album_key(message.chat.id, message.media_group_id)
    batch = await services.albums.collect(key, message)
    if batch is None:
        return
    items = media_utils.media_items_from_messages(batch)
    if not items:
        await message.answer(UNSUPPORTED_TEXT)
        return
    await _create_draft(
        message,
        services,
        state,
        text=media_utils.album_caption(batch),
        items=items,
    )


async def handle_media(
    message: Message, services: TelegramServices, state: FSMContext
) -> None:
    item = media_utils.media_item_from_message(message)
    if item is None:
        await message.answer(UNSUPPORTED_TEXT)
        return
    await _create_draft(
        message,
        services,
        state,
        text=media_utils.message_text(message),
        items=[item],
    )


async def handle_unknown_command(message: Message) -> None:
    """A command never becomes a submission, even if the bot does not know it."""
    await message.answer(
        "Неизвестная команда. /start — как отправить заявку."
    )


async def handle_text(
    message: Message, services: TelegramServices, state: FSMContext
) -> None:
    await _create_draft(
        message, services, state, text=message.text, items=[]
    )


async def handle_unsupported(message: Message) -> None:
    await message.answer(UNSUPPORTED_TEXT)


# --- draft buttons -----------------------------------------------------------


async def choose_privacy(
    callback: CallbackQuery,
    callback_data: DraftCallback,
    services: TelegramServices,
) -> None:
    submission = await _load_draft(callback, services, callback_data.submission_id)
    if submission is None:
        return
    updated = await services.submissions.set_privacy(
        callback_data.submission_id,
        want_anonymous=callback_data.action == DRAFT_ANON,
    )
    await callback.answer(
        "Публикуем анонимно"
        if updated.want_anonymous
        else "Публикуем с вашим именем"
    )
    await _refresh_draft(callback, updated)


async def edit_draft(
    callback: CallbackQuery,
    callback_data: DraftCallback,
    services: TelegramServices,
    state: FSMContext,
) -> None:
    submission = await _load_draft(callback, services, callback_data.submission_id)
    if submission is None:
        return
    await state.set_state(DraftFlow.editing_text)
    await state.update_data(editing_id=submission.id, draft_id=submission.id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Пришлите новый текст заявки (/cancel — отмена)."
        )


async def submit_draft(
    callback: CallbackQuery,
    callback_data: DraftCallback,
    services: TelegramServices,
    state: FSMContext,
) -> None:
    submission = await _load_draft(callback, services, callback_data.submission_id)
    if submission is None:
        return
    user = callback.from_user

    if services.is_blocked(user.id):
        await callback.answer(BLOCKED_TEXT, show_alert=True)
        return

    if submission.want_anonymous is None:
        await callback.answer(
            "Сначала выберите: анонимно или с вашим именем.", show_alert=True
        )
        return

    decision = services.antiflood.decide(Platform.telegram, str(user.id))
    if not decision.allowed:
        await callback.answer(
            "Слишком много заявок подряд. "
            f"Попробуйте через {decision.window_sec} секунд.",
            show_alert=True,
        )
        return

    try:
        submitted = await services.submissions.submit(submission.id or 0)
    except ValueError:
        await callback.answer(
            "Заявка пустая: добавьте текст или медиа.", show_alert=True
        )
        return

    await state.set_state(None)
    await state.update_data(draft_id=None)
    await callback.answer("Отправлено")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                f"📨 Заявка #{submitted.id} в очереди на модерацию.\n"
                f"Публикация: {privacy_label(submitted)}."
            )
        except Exception:  # noqa: BLE001 - message may be too old to edit
            logger.debug("Карточка черновика %s не обновлена", submitted.id)


async def cancel_draft(
    callback: CallbackQuery,
    callback_data: DraftCallback,
    services: TelegramServices,
    state: FSMContext,
) -> None:
    submission = await _load_draft(callback, services, callback_data.submission_id)
    if submission is None:
        return
    try:
        await services.submissions.cancel_draft(callback_data.submission_id)
    except ValueError:
        await callback.answer(ALREADY_SENT_TEXT, show_alert=True)
        return
    await state.set_state(None)
    await state.update_data(draft_id=None)
    await callback.answer("Черновик отменён")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text("🗑 Черновик отменён.")
        except Exception:  # noqa: BLE001 - message may be too old to edit
            logger.debug("Карточка черновика %s не обновлена", submission.id)


def build_user_router() -> Router:
    """Fresh router per call; message order is the routing order."""
    router = Router(name="tg-user")
    router.message.filter(F.chat.type == ChatType.PRIVATE)

    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_cancel, Command("cancel"))
    router.message.register(
        edit_draft_text,
        DraftFlow.editing_text,
        F.text,
        ~F.text.startswith("/"),
    )
    router.message.register(handle_album, F.media_group_id)
    router.message.register(
        handle_media, F.photo | F.video | F.animation | F.sticker | F.document
    )
    router.message.register(handle_unknown_command, F.text.startswith("/"))
    router.message.register(handle_text, F.text)
    router.message.register(handle_unsupported)

    router.callback_query.register(
        choose_privacy,
        DraftCallback.filter(F.action.in_({DRAFT_ANON, DRAFT_NAMED})),
    )
    router.callback_query.register(
        edit_draft, DraftCallback.filter(F.action == DRAFT_EDIT)
    )
    router.callback_query.register(
        submit_draft, DraftCallback.filter(F.action == DRAFT_SUBMIT)
    )
    router.callback_query.register(
        cancel_draft, DraftCallback.filter(F.action == DRAFT_CANCEL)
    )
    return router
