"""Admin free-text steps: reject reason and a direct reply to the author."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.adapters.telegram.deps import TelegramServices, is_telegram_admin
from bot.adapters.telegram.event_sync import author_chat_id, notify_author
from bot.adapters.telegram.states import AdminReject, AdminReply
from bot.core import Platform, Source

logger = logging.getLogger(__name__)

NOT_FOUND_TEXT = "Заявка не найдена."
HANDLED_TEXT = "Заявка уже обработана."
EXPIRED_TEXT = "Сессия истекла, начните заново с карточки заявки."
DISCORD_AUTHOR_TEXT = (
    "Автор заявки пришёл из Discord — ответьте ему в Discord."
)


async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.set_state(None)
    await message.answer("Действие отменено.")


async def reject_with_reason(
    message: Message, services: TelegramServices, state: FSMContext
) -> None:
    data = await state.get_data()
    submission_id = data.get("submission_id")
    if submission_id is None:
        await state.set_state(None)
        await message.answer(EXPIRED_TEXT)
        return

    submission = services.submissions.get(int(submission_id))
    if submission is None:
        await state.set_state(None)
        await message.answer(NOT_FOUND_TEXT)
        return

    result = await services.moderation.reject(
        int(submission_id),
        reason=message.text,
        moderator_platform=Platform.telegram,
        moderator_id=str(message.from_user.id) if message.from_user else None,
    )
    await state.set_state(None)
    if result.already_handled:
        await message.answer(HANDLED_TEXT)
        return

    # The author DM and the card refresh happen in event_sync on the bus event.
    if result.submission.source is Source.telegram:
        await message.answer(f"Заявка #{submission_id} отклонена, автор уведомлён.")
    else:
        await message.answer(
            f"Заявка #{submission_id} отклонена. {DISCORD_AUTHOR_TEXT}"
        )


async def reply_to_author(
    message: Message, bot: Bot, services: TelegramServices, state: FSMContext
) -> None:
    data = await state.get_data()
    submission_id = data.get("submission_id")
    if submission_id is None:
        await state.set_state(None)
        await message.answer(EXPIRED_TEXT)
        return

    submission = services.submissions.get(int(submission_id))
    if submission is None:
        await state.set_state(None)
        await message.answer(NOT_FOUND_TEXT)
        return

    if author_chat_id(submission) is None:
        await state.set_state(None)
        await message.answer(DISCORD_AUTHOR_TEXT)
        return

    delivered = await notify_author(
        bot, submission, f"💬 Ответ администратора:\n\n{message.text}"
    )
    if not delivered:
        await message.answer(
            "Не доставлено: автор закрыл личные сообщения боту."
        )
        return

    # State is kept so the admin can send several messages in a row.
    await message.answer("Ответ отправлен автору.")


def build_reply_router() -> Router:
    """Fresh router per call, so several bridges can live in one process."""
    router = Router(name="tg-reply")
    router.message.filter(is_telegram_admin)
    router.message.register(cmd_cancel, Command("cancel"))
    router.message.register(
        reject_with_reason,
        AdminReject.waiting_reason,
        F.text,
        ~F.text.startswith("/"),
    )
    router.message.register(
        reply_to_author,
        AdminReply.waiting_text,
        F.text,
        ~F.text.startswith("/"),
    )
    return router
