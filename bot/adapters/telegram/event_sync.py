"""EventBus wiring: keep Telegram cards and authors in sync with the core.

Events are platform-agnostic, so a decision taken in Discord repaints the
Telegram cards and notifies a Telegram author just as a local decision does.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.adapters.telegram.cards import TelegramCards
from bot.core import (
    EventBus,
    Source,
    Submission,
    SubmissionApproved,
    SubmissionPublished,
    SubmissionRejected,
    SubmissionScheduled,
    SubmissionStatus,
    SubmissionSubmitted,
    SubmissionUpdated,
)

logger = logging.getLogger(__name__)

QUEUED_TEXT = "📨 Заявка в очереди на модерацию."
PUBLISHED_TEXT = "✅ Ваша заявка опубликована в канале."
REJECTED_TEXT = "❌ Ваша заявка отклонена."


def author_chat_id(submission: Submission) -> int | None:
    """Telegram chat of the author, or None for submissions from Discord."""
    if submission.source is not Source.telegram:
        return None
    try:
        return int(submission.author_platform_user_id)
    except (TypeError, ValueError):
        return None


async def notify_author(bot: Bot, submission: Submission, text: str) -> bool:
    """Best-effort DM to the author; a user who blocked the bot is not an error."""
    chat_id = author_chat_id(submission)
    if chat_id is None:
        return False
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError:
        logger.info("Автор %s не получил уведомление", chat_id)
        return False
    return True


def rejection_text(submission: Submission, reason: str | None = None) -> str | None:
    """Silent rejects carry no reason and the author is not notified at all."""
    detail = (reason or submission.reject_reason or "").strip()
    if not detail:
        return None
    return f"{REJECTED_TEXT}\n\nПричина: {detail}"


class TelegramEventSync:
    def __init__(self, bot: Bot, *, cards: TelegramCards) -> None:
        self.bot = bot
        self.cards = cards
        self._subscriptions = (
            (SubmissionSubmitted, self.on_submitted),
            (SubmissionUpdated, self.on_updated),
            (SubmissionApproved, self.on_approved),
            (SubmissionScheduled, self.on_scheduled),
            (SubmissionRejected, self.on_rejected),
            (SubmissionPublished, self.on_published),
        )

    def attach(self, bus: EventBus) -> None:
        for event_type, handler in self._subscriptions:
            bus.subscribe(event_type, handler)

    def detach(self, bus: EventBus) -> None:
        for event_type, handler in self._subscriptions:
            bus.unsubscribe(event_type, handler)

    async def on_submitted(self, event: SubmissionSubmitted) -> None:
        await self.cards.send_cards(event.submission)

    async def on_updated(self, event: SubmissionUpdated) -> None:
        submission = event.submission
        if submission.status not in (
            SubmissionStatus.pending,
            SubmissionStatus.scheduled,
            SubmissionStatus.approved,
        ):
            return
        await self.cards.update_cards(submission)

    async def on_approved(self, event: SubmissionApproved) -> None:
        await self.cards.update_cards(event.submission)

    async def on_scheduled(self, event: SubmissionScheduled) -> None:
        await self.cards.update_cards(event.submission)

    async def on_rejected(self, event: SubmissionRejected) -> None:
        await self.cards.update_cards(event.submission)
        text = rejection_text(event.submission, event.reason)
        if text is not None:
            await notify_author(self.bot, event.submission, text)

    async def on_published(self, event: SubmissionPublished) -> None:
        await self.cards.update_cards(event.submission)
        await notify_author(self.bot, event.submission, PUBLISHED_TEXT)
