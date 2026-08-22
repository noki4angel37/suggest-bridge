"""EventBus wiring: domain events -> Discord cards, reactions and DMs.

Moderation decisions taken in Telegram reach Discord through the same events,
so a card stays in sync no matter where the decision was made.
"""

from __future__ import annotations

import logging

import discord

from bot.adapters.discord import moderation, texts
from bot.adapters.discord.context import DiscordContext
from bot.core.models import (
    Source,
    Submission,
    SubmissionApproved,
    SubmissionPublished,
    SubmissionRejected,
    SubmissionScheduled,
    SubmissionStatus,
    SubmissionSubmitted,
    SubmissionUpdated,
    utcnow,
)

logger = logging.getLogger(__name__)


class DiscordEventSync:
    """Keeps Discord in sync with the core domain events."""

    def __init__(self, bot: discord.Client, ctx: DiscordContext) -> None:
        self.bot = bot
        self.ctx = ctx
        self._subscriptions = (
            (SubmissionSubmitted, self.on_submitted),
            (SubmissionUpdated, self.on_updated),
            (SubmissionApproved, self.on_approved),
            (SubmissionRejected, self.on_rejected),
            (SubmissionScheduled, self.on_scheduled),
            (SubmissionPublished, self.on_published),
        )

    def register(self) -> None:
        for event_type, handler in self._subscriptions:
            self.ctx.services.bus.subscribe(event_type, handler)  # type: ignore[arg-type]

    def unregister(self) -> None:
        for event_type, handler in self._subscriptions:
            self.ctx.services.bus.unsubscribe(event_type, handler)  # type: ignore[arg-type]

    # --- handlers ------------------------------------------------------------

    async def on_submitted(self, event: SubmissionSubmitted) -> None:
        submission = event.submission
        if not self.ctx.mirrors(submission.source):
            logger.debug(
                "Пропуск Discord-карточки для source=%s (mirror_sources)",
                submission.source,
            )
            return
        await self.bot.wait_until_ready()
        message = await moderation.post_moderation_card(
            self.bot, self.ctx, submission
        )
        if message is None:
            logger.warning(
                "Не удалось опубликовать Discord-карточку заявки %s (source=%s)",
                submission.id,
                submission.source,
            )
        await moderation.set_status_reaction(self.bot, submission)

    async def on_updated(self, event: SubmissionUpdated) -> None:
        """Repaint mod cards after moderator text edits (or other field changes)."""
        submission = event.submission
        if submission.status not in (
            SubmissionStatus.pending,
            SubmissionStatus.scheduled,
            SubmissionStatus.approved,
        ):
            return
        await self.reflect(submission)

    async def on_approved(self, event: SubmissionApproved) -> None:
        submission = event.submission
        await self.reflect(submission)
        # A scheduled approval is announced by the scheduled event instead.
        if submission.status is SubmissionStatus.scheduled:
            return
        await self.notify_author(
            submission, texts.notify_approved(int(submission.id or 0))
        )

    async def on_rejected(self, event: SubmissionRejected) -> None:
        submission = event.submission
        await self.reflect(submission)
        await self.notify_author(
            submission,
            texts.notify_rejected(
                int(submission.id or 0),
                event.reason or submission.reject_reason,
            ),
        )

    async def on_scheduled(self, event: SubmissionScheduled) -> None:
        submission = event.submission
        await self.reflect(submission)
        moment = event.scheduled_at or submission.scheduled_at
        submission_id = int(submission.id or 0)
        # An approval without a delay is scheduled for "now" so the publish
        # scheduler takes it: the author should still read it as approved.
        immediate = moment is None or moment <= utcnow()
        await self.notify_author(
            submission,
            texts.notify_approved(submission_id)
            if immediate
            else texts.notify_scheduled(
                submission_id, moderation.format_moment(moment)
            ),
        )

    async def on_published(self, event: SubmissionPublished) -> None:
        submission = event.submission
        await self.reflect(submission)
        await self.notify_author(
            submission, texts.notify_published(int(submission.id or 0))
        )

    # --- helpers -------------------------------------------------------------

    async def reflect(self, submission: Submission) -> None:
        """Update mod cards and the status emoji on the original message."""
        await self.bot.wait_until_ready()
        await moderation.update_moderation_cards(
            self.bot, self.ctx, submission
        )
        await moderation.set_status_reaction(self.bot, submission)

    async def notify_author(
        self, submission: Submission, message: str
    ) -> None:
        if submission.source is not Source.discord:
            return
        delivered = await moderation.send_author_dm(
            self.bot, submission, message
        )
        if not delivered:
            logger.info(
                "Автор заявки %s не получил личное сообщение", submission.id
            )
