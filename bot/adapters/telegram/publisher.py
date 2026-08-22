"""Publishing approved submissions into the Telegram channel.

The sending itself lives in `channel_publish`, which executes the core
`PublishPlan`; this module keeps the Telegram-facing helpers and the
`publish → mark_published` shortcut used by the moderation handlers.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo

from bot.adapters.telegram.channel_publish import (
    PublishResult,
    TelegramChannelPublisher,
)
from bot.core import (
    ContentType,
    MediaItem,
    ModerationService,
    Platform,
    RefKind,
    Submission,
    build_publish_plan,
    resolve_with_author,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PublishResult",
    "TelegramPublisher",
    "channel_caption",
    "file_input",
    "input_media",
    "with_author_for",
]


def with_author_for(submission: Submission) -> bool:
    """Anonymous unless the author (or a moderator) explicitly asked otherwise."""
    return resolve_with_author(submission)


def channel_caption(submission: Submission, *, with_author: bool) -> str:
    """Channel body from core rules plus links that are not part of the text."""
    return build_publish_plan(submission, with_author=with_author).caption


def file_input(item: MediaItem) -> str | FSInputFile:
    """Telegram accepts a file_id or an uploaded local file.

    Discord CDN URLs are not valid here: Telegram fetches them itself and
    gets 404 after the source Discord message is deleted.
    """
    if item.ref_kind is RefKind.local_path and item.local_path:
        return FSInputFile(item.local_path)
    if item.ref_kind is RefKind.telegram_file_id and item.file_id:
        return item.file_id
    raise ValueError(
        f"Медиа без файла для Telegram: {item.content_type.value} "
        f"({item.ref_kind.value if item.ref_kind else 'none'})"
    )


def input_media(item: MediaItem, caption: str | None = None) -> Any:
    """One album entry; photos and videos may be mixed inside a media group."""
    factory = (
        InputMediaPhoto if item.content_type is ContentType.photo else InputMediaVideo
    )
    return factory(media=file_input(item), caption=caption, parse_mode=None)


class TelegramPublisher:
    """Sends a submission to the channel; never decides moderation outcomes."""

    def __init__(
        self,
        bot: Bot,
        channel_id: int | str,
        *,
        moderation: ModerationService | None = None,
    ) -> None:
        self.bot = bot
        self.channel_id = channel_id
        self.moderation = moderation
        self.channel = TelegramChannelPublisher(bot, channel_id)

    async def publish(
        self, submission: Submission, *, with_author: bool | None = None
    ) -> PublishResult:
        return await self.channel.publish(submission, with_author=with_author)

    async def publish_and_mark(
        self, submission: Submission, *, with_author: bool | None = None
    ) -> PublishResult:
        """Publish, then move the submission to `published` via the core service."""
        result = await self.publish(submission, with_author=with_author)
        if self.moderation is not None and submission.id is not None:
            await self.moderation.mark_published(
                submission.id,
                platform=Platform.telegram,
                target_id=result.chat_id,
                message_id=result.message_id,
            )
        return result
