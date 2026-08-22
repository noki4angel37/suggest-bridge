"""Telegram content helpers: media items, album grouping, links.

Everything here is deliberately duck-typed over aiogram objects so the logic
stays unit-testable without a live Bot or real `Message` instances.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from bot.core import ContentType, MediaItem, Submission

# Telegram sends album items as separate updates; we wait a bit to collect them.
ALBUM_FLUSH_DELAY = 1.0
# Telegram accepts at most 10 items per media group.
MEDIA_GROUP_LIMIT = 10

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_URL_TRAILING = ".,;:!?»\"'"


def extract_urls(text: str | None) -> list[str]:
    """Ordered unique URLs found in a message text or caption."""
    if not text:
        return []
    found: list[str] = []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(_URL_TRAILING)
        if url and url not in found:
            found.append(url)
    return found


def link_items(text: str | None, *, start_index: int = 0) -> list[MediaItem]:
    """Links are stored as media items without a file ref (published as text)."""
    return [
        MediaItem(
            content_type=ContentType.link,
            order_index=start_index + index,
            caption=url,
        )
        for index, url in enumerate(extract_urls(text))
    ]


def message_text(message: Any) -> str | None:
    value = getattr(message, "text", None) or getattr(message, "caption", None)
    value = (value or "").strip()
    return value or None


def media_item_from_message(
    message: Any, *, order_index: int = 0
) -> MediaItem | None:
    """Map a Telegram message onto a single `MediaItem`, if it carries media."""
    caption = (getattr(message, "caption", None) or "").strip() or None

    photos = getattr(message, "photo", None)
    if photos:
        return MediaItem(
            content_type=ContentType.photo,
            order_index=order_index,
            file_id=photos[-1].file_id,
            caption=caption,
        )

    video = getattr(message, "video", None)
    if video is not None:
        return MediaItem(
            content_type=ContentType.video,
            order_index=order_index,
            file_id=video.file_id,
            caption=caption,
        )

    # GIFs arrive as animations; the channel shows them like videos.
    animation = getattr(message, "animation", None)
    if animation is not None:
        return MediaItem(
            content_type=ContentType.video,
            order_index=order_index,
            file_id=animation.file_id,
            caption=caption,
        )

    sticker = getattr(message, "sticker", None)
    if sticker is not None:
        return MediaItem(
            content_type=ContentType.sticker,
            order_index=order_index,
            file_id=sticker.file_id,
        )

    document = getattr(message, "document", None)
    if document is not None:
        mime = (getattr(document, "mime_type", None) or "").lower()
        name = (getattr(document, "file_name", None) or "").lower()
        image_suffix = name.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        )
        if mime.startswith("image/") or image_suffix:
            return MediaItem(
                content_type=ContentType.photo,
                order_index=order_index,
                file_id=document.file_id,
                caption=caption,
            )

    return None


def sort_album(messages: list[Any]) -> list[Any]:
    return sorted(messages, key=lambda item: getattr(item, "message_id", 0))


def media_items_from_messages(messages: list[Any]) -> list[MediaItem]:
    """Album items in Telegram order, re-indexed from zero."""
    items: list[MediaItem] = []
    for message in sort_album(messages):
        item = media_item_from_message(message, order_index=len(items))
        if item is not None:
            items.append(item)
    return items


def album_caption(messages: list[Any]) -> str | None:
    """Telegram puts the album caption on one item only — find it."""
    for message in sort_album(messages):
        caption = (getattr(message, "caption", None) or "").strip()
        if caption:
            return caption
    return None


@dataclass(frozen=True)
class MediaSplit:
    """Media grouped by how Telegram wants it sent."""

    visual: list[MediaItem] = field(default_factory=list)
    stickers: list[MediaItem] = field(default_factory=list)
    links: list[MediaItem] = field(default_factory=list)

    @property
    def has_media(self) -> bool:
        return bool(self.visual or self.stickers)

    @property
    def is_single_visual(self) -> bool:
        return len(self.visual) == 1 and not self.stickers


def split_media(submission: Submission) -> MediaSplit:
    visual: list[MediaItem] = []
    stickers: list[MediaItem] = []
    links: list[MediaItem] = []
    for item in sorted(submission.media, key=lambda i: i.order_index):
        if item.content_type is ContentType.sticker:
            stickers.append(item)
        elif item.content_type is ContentType.link:
            links.append(item)
        elif item.file_ref:
            visual.append(item)
    return MediaSplit(visual=visual, stickers=stickers, links=links)


def chunk_media(items: list[MediaItem]) -> list[list[MediaItem]]:
    return [
        items[start : start + MEDIA_GROUP_LIMIT]
        for start in range(0, len(items), MEDIA_GROUP_LIMIT)
    ]


def album_key(chat_id: Any, media_group_id: Any) -> str:
    return f"{chat_id}:{media_group_id}"


class AlbumBuffer:
    """Collects album messages: the first message flushes the whole batch.

    The handler that receives the first item of a media group waits
    `delay` seconds and then gets every message of that album; handlers for
    the remaining items get `None` and simply return.
    """

    def __init__(self, *, delay: float = ALBUM_FLUSH_DELAY) -> None:
        self.delay = delay
        self._groups: dict[str, list[Any]] = {}
        self._leaders: set[str] = set()
        self._lock = asyncio.Lock()

    async def collect(self, key: str, message: Any) -> list[Any] | None:
        async with self._lock:
            self._groups.setdefault(key, []).append(message)
            is_leader = key not in self._leaders
            if is_leader:
                self._leaders.add(key)
        if not is_leader:
            return None

        await asyncio.sleep(self.delay)
        async with self._lock:
            self._leaders.discard(key)
            return sort_album(self._groups.pop(key, []))
