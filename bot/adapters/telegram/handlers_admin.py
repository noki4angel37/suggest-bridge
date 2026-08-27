"""Admin moderation callbacks: approve, publish, schedule, reject."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.adapters.telegram import keyboards
from bot.adapters.telegram.channel_publish import ChannelPublishError
from bot.adapters.telegram.deps import TelegramServices, is_telegram_admin
from bot.adapters.telegram.keyboards import (
    MOD_ACCEPT,
    MOD_BACK,
    MOD_EDIT,
    MOD_PUBLISH_ANON,
    MOD_PUBLISH_NAMED,
    MOD_REJECT,
    MOD_REJECT_REASON,
    MOD_REJECT_SILENT,
    MOD_REPLY,
    MOD_SCHEDULE,
    MOD_TARGET_BOTH,
    MOD_TARGET_DS,
    MOD_TARGET_TG,
    ModerationCallback,
)
from bot.adapters.telegram.states import (
    AdminEdit,
    AdminReject,
    AdminReply,
    AdminSchedule,
)
from bot.core import (
    Platform,
    PublishTarget,
    Submission,
    SubmissionStatus,
    finalize_approval,
    is_handled,
    resolve_with_author,
)
from bot.core.publish_router import PublishRouterError

logger = logging.getLogger(__name__)

PUBLISH_ERRORS = (
    TelegramAPIError,
    ChannelPublishError,
    PublishRouterError,
    ValueError,
)
NOT_FOUND_TEXT = "Заявка не найдена."
HANDLED_TEXT = "Заявка уже обработана."
SCHEDULE_FORMATS = (
    "Форматы: «15:30», «12.08 15:30», «12.08.2026 15:30», «+30м», «+2ч»."
)

_REL_RE = re.compile(r"^\+\s*(\d+)\s*(м|мин\w*|ч|час\w*)?$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_RE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?[\s,]+(\d{1,2}):(\d{2})$"
)


def parse_schedule_at(raw: str | None, *, now: datetime | None = None) -> datetime | None:
    """Parse an admin-typed publish time into an aware UTC datetime.

    Naive input is read in the bot process local timezone.
    """
    text = (raw or "").strip().lower().replace("ё", "е")
    if not text:
        return None
    base = (now or datetime.now()).astimezone()

    relative = _REL_RE.match(text)
    if relative:
        amount = int(relative.group(1))
        if amount <= 0:
            return None
        unit = relative.group(2) or "м"
        delta = (
            timedelta(hours=amount)
            if unit.startswith("ч")
            else timedelta(minutes=amount)
        )
        return (base + delta).astimezone(timezone.utc)

    daily = _TIME_RE.match(text)
    if daily:
        hour, minute = int(daily.group(1)), int(daily.group(2))
        if hour > 23 or minute > 59:
            return None
        moment = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if moment <= base:
            moment += timedelta(days=1)
        return moment.astimezone(timezone.utc)

    dated = _DATE_RE.match(text)
    if dated:
        day, month = int(dated.group(1)), int(dated.group(2))
        raw_year = dated.group(3)
        year = int(raw_year) if raw_year else base.year
        if year < 100:
            year += 2000
        hour, minute = int(dated.group(4)), int(dated.group(5))
        if hour > 23 or minute > 59:
            return None
        try:
            moment = base.replace(
                year=year,
                month=month,
                day=day,
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
        except ValueError:
            return None
        if moment <= base and not raw_year:
            try:
                moment = moment.replace(year=year + 1)
            except ValueError:
                return None
        if moment <= base:
            return None
        return moment.astimezone(timezone.utc)

    return None


def format_local(moment: datetime) -> str:
    return moment.astimezone().strftime("%d.%m.%Y %H:%M")


async def _swap_markup(
    callback: CallbackQuery, markup: InlineKeyboardMarkup
) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=markup)
    except TelegramAPIError:
        logger.debug("Клавиатура карточки не обновлена")


async def _load_open(
    callback: CallbackQuery, services: TelegramServices, submission_id: int
) -> Submission | None:
    """Submission that still accepts a decision (published/rejected are final)."""
    submission = services.submissions.get(submission_id)
    if submission is None:
        await callback.answer(NOT_FOUND_TEXT, show_alert=True)
        return None
    if submission.status in (
        SubmissionStatus.published,
        SubmissionStatus.rejected,
    ):
        await callback.answer(HANDLED_TEXT, show_alert=True)
        await services.cards.update_cards(submission)
        return None
    return submission


# --- decision menus ----------------------------------------------------------


async def accept_prompt(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
) -> None:
    submission = await _load_open(callback, services, callback_data.submission_id)
    if submission is None:
        return
    await callback.answer()
    await _swap_markup(
        callback,
        keyboards.publish_keyboard(
            callback_data.submission_id,
            publish_target=submission.publish_target.value,
        ),
    )


async def set_publish_target(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
    target: PublishTarget,
) -> None:
    submission = await _load_open(callback, services, callback_data.submission_id)
    if submission is None:
        return
    updated = await services.submissions.set_publish_target(
        callback_data.submission_id, target
    )
    await callback.answer(f"Назначение: {target.value}")
    await _swap_markup(
        callback,
        keyboards.publish_keyboard(
            callback_data.submission_id,
            publish_target=updated.publish_target.value,
        ),
    )


async def target_telegram(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
) -> None:
    await set_publish_target(
        callback, callback_data, services, PublishTarget.telegram
    )


async def target_discord(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
) -> None:
    await set_publish_target(
        callback, callback_data, services, PublishTarget.discord
    )


async def target_both(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
) -> None:
    await set_publish_target(
        callback, callback_data, services, PublishTarget.both
    )


async def reject_prompt(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
) -> None:
    if await _load_open(callback, services, callback_data.submission_id) is None:
        return
    await callback.answer()
    await _swap_markup(
        callback, keyboards.reject_keyboard(callback_data.submission_id)
    )


async def back_to_moderation(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    state: FSMContext,
) -> None:
    await state.set_state(None)
    await callback.answer()
    await _swap_markup(
        callback, keyboards.moderation_keyboard(callback_data.submission_id)
    )


# --- publish -----------------------------------------------------------------


async def _approve_and_publish(
    callback: CallbackQuery,
    services: TelegramServices,
    submission_id: int,
    *,
    with_author: bool,
) -> None:
    submission = await _load_open(callback, services, submission_id)
    if submission is None:
        return
    moderator_id = str(callback.from_user.id) if callback.from_user else None
    await callback.answer("Публикую…")

    async def publish_now(target: Submission) -> object:
        return await services.publish_submission(target, with_author=with_author)

    try:
        if is_handled(submission.status):
            # Already approved or scheduled: the moderator retries a failed
            # publish or overrides the schedule, so publish right now.
            await services.submissions.set_privacy(
                submission_id, want_anonymous=not with_author
            )
            refreshed = services.submissions.get(submission_id) or submission
            published_ref = await services.publish_submission(
                refreshed, with_author=with_author
            )
            from bot.core.publisher import extract_publish_ref

            target_id, message_id = extract_publish_ref(published_ref)
            await services.moderation.mark_published(
                submission_id,
                platform=Platform.telegram,
                target_id=target_id,
                message_id=message_id,
            )
        else:
            await finalize_approval(
                services.moderation,
                submission_id=submission_id,
                with_author=with_author,
                publish_at=None,
                publish_now_cb=publish_now,
                submissions=services.submissions,
                moderator_platform=Platform.telegram,
                moderator_id=moderator_id,
                platform=Platform.telegram,
            )
    except PUBLISH_ERRORS as exc:
        logger.exception("Публикация заявки %s не удалась", submission_id)
        await callback.answer(f"Ошибка публикации: {exc}"[:190], show_alert=True)
        await services.cards.update_cards(
            services.submissions.get(submission_id) or submission,
            status_line="⚠️ Ошибка публикации, попробуйте снова",
        )


async def publish_anonymous(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
) -> None:
    await _approve_and_publish(
        callback, services, callback_data.submission_id, with_author=False
    )


async def publish_with_name(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
) -> None:
    await _approve_and_publish(
        callback, services, callback_data.submission_id, with_author=True
    )


# --- schedule ----------------------------------------------------------------


async def schedule_prompt(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
    state: FSMContext,
) -> None:
    submission = await _load_open(callback, services, callback_data.submission_id)
    if submission is None:
        return
    await state.set_state(AdminSchedule.waiting_datetime)
    await state.update_data(submission_id=submission.id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Когда опубликовать заявку #{submission.id}?\n\n"
            f"{SCHEDULE_FORMATS}\n/cancel — отмена.",
            reply_markup=keyboards.schedule_prompt_keyboard(submission.id or 0),
        )


async def schedule_apply(
    message: Message, services: TelegramServices, state: FSMContext
) -> None:
    data = await state.get_data()
    submission_id = data.get("submission_id")
    if submission_id is None:
        await state.set_state(None)
        await message.answer("Сессия отложенной публикации истекла.")
        return

    moment = parse_schedule_at(message.text)
    if moment is None:
        await message.answer(f"Не понял время. {SCHEDULE_FORMATS}")
        return

    submission = services.submissions.get(int(submission_id))
    if submission is None:
        await state.set_state(None)
        await message.answer(NOT_FOUND_TEXT)
        return

    # Without an explicit choice the post keeps the author's wish (anonymous
    # when nothing was chosen at all).
    with_author = resolve_with_author(submission)
    if is_handled(submission.status):
        already = await services.moderation.schedule(int(submission_id), moment)
        handled = already.already_handled
    else:
        outcome = await finalize_approval(
            services.moderation,
            submission_id=int(submission_id),
            with_author=with_author,
            publish_at=moment,
            submissions=services.submissions,
            moderator_platform=Platform.telegram,
            moderator_id=str(message.from_user.id) if message.from_user else None,
            platform=Platform.telegram,
        )
        handled = outcome.already_handled

    await state.set_state(None)
    if handled:
        await message.answer(HANDLED_TEXT)
        return
    await message.answer(
        f"🕓 Заявка #{submission_id} отложена до {format_local(moment)}."
    )


# --- reject ------------------------------------------------------------------


async def reject_silent(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
) -> None:
    submission = await _load_open(callback, services, callback_data.submission_id)
    if submission is None:
        return
    moderator_id = str(callback.from_user.id) if callback.from_user else None
    result = await services.moderation.reject(
        callback_data.submission_id,
        moderator_platform=Platform.telegram,
        moderator_id=moderator_id,
    )
    if result.already_handled:
        await callback.answer(HANDLED_TEXT, show_alert=True)
        await services.cards.update_cards(result.submission)
        return
    await callback.answer("Отклонено без уведомления автора")


async def reject_reason_prompt(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
    state: FSMContext,
) -> None:
    submission = await _load_open(callback, services, callback_data.submission_id)
    if submission is None:
        return
    await state.set_state(AdminReject.waiting_reason)
    await state.update_data(submission_id=submission.id, fsm_started_at=time.time())
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Причина отклонения заявки #{submission.id}? "
            "Автор получит её в личных сообщениях.\n/cancel — отмена."
        )


async def reply_prompt(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
    state: FSMContext,
) -> None:
    submission = services.submissions.get(callback_data.submission_id)
    if submission is None:
        await callback.answer(NOT_FOUND_TEXT, show_alert=True)
        return
    await state.set_state(AdminReply.waiting_text)
    await state.update_data(submission_id=submission.id, fsm_started_at=time.time())
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Что ответить автору заявки #{submission.id}?\n/cancel — отмена."
        )


async def edit_prompt(
    callback: CallbackQuery,
    callback_data: ModerationCallback,
    services: TelegramServices,
    state: FSMContext,
) -> None:
    submission = services.submissions.get(callback_data.submission_id)
    if submission is None:
        await callback.answer(NOT_FOUND_TEXT, show_alert=True)
        return
    if submission.status not in (
        SubmissionStatus.pending,
        SubmissionStatus.scheduled,
        SubmissionStatus.approved,
    ):
        await callback.answer(
            "Текст можно править только до публикации.", show_alert=True
        )
        return
    await state.set_state(AdminEdit.waiting_text)
    await state.update_data(submission_id=submission.id)
    await callback.answer()
    current = (submission.text or "").strip() or "—"
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Новый текст заявки #{submission.id}?\n"
            f"Сейчас: {current[:200]}\n\n/cancel — отмена."
        )


async def edit_apply(
    message: Message, services: TelegramServices, state: FSMContext
) -> None:
    data = await state.get_data()
    submission_id = data.get("submission_id")
    if submission_id is None:
        await state.set_state(None)
        await message.answer("Сессия правки истекла.")
        return
    try:
        updated = await services.submissions.edit_moderator_text(
            int(submission_id), message.text
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    except Exception:  # noqa: BLE001
        await state.set_state(None)
        await message.answer(NOT_FOUND_TEXT)
        return
    await state.set_state(None)
    await services.cards.update_cards(updated)
    await message.answer(f"✏️ Текст заявки #{submission_id} обновлён.")


def build_admin_router() -> Router:
    """Fresh router per call, so several bridges can live in one process."""
    router = Router(name="tg-admin")
    router.callback_query.filter(is_telegram_admin)
    router.message.filter(is_telegram_admin)

    for action, handler in (
        (MOD_ACCEPT, accept_prompt),
        (MOD_REJECT, reject_prompt),
        (MOD_BACK, back_to_moderation),
        (MOD_PUBLISH_ANON, publish_anonymous),
        (MOD_PUBLISH_NAMED, publish_with_name),
        (MOD_TARGET_TG, target_telegram),
        (MOD_TARGET_DS, target_discord),
        (MOD_TARGET_BOTH, target_both),
        (MOD_SCHEDULE, schedule_prompt),
        (MOD_REJECT_SILENT, reject_silent),
        (MOD_REJECT_REASON, reject_reason_prompt),
        (MOD_REPLY, reply_prompt),
        (MOD_EDIT, edit_prompt),
    ):
        router.callback_query.register(
            handler, ModerationCallback.filter(F.action == action)
        )

    router.message.register(
        schedule_apply,
        AdminSchedule.waiting_datetime,
        F.text,
        ~F.text.startswith("/"),
    )
    router.message.register(
        edit_apply,
        AdminEdit.waiting_text,
        F.text,
        ~F.text.startswith("/"),
    )
    return router
