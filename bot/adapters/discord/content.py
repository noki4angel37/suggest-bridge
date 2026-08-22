"""Turning Discord message payloads into core `MediaItem` lists.

Pure helpers: discord.py objects are converted to `AttachmentInfo` by the
caller, so classification is testable without a Discord connection.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from bot.core.models import ContentType, MediaItem

IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".apng"}
)
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"})

URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class AttachmentInfo:
    """Flat view of `discord.Attachment` / sticker."""

    url: str
    content_type: str | None = None
    filename: str | None = None


def classify_attachment(
    content_type: str | None = None, filename: str | None = None
) -> ContentType:
    """Map a Discord attachment to a core content type.

    Documents and audio have no core counterpart, so they are stored as a link
    to the attachment url and still travel to the channel post.
    """
    mime = (content_type or "").lower()
    if mime.startswith("image/"):
        return ContentType.photo
    if mime.startswith("video/"):
        return ContentType.video

    suffix = PurePosixPath((filename or "").lower()).suffix
    if suffix in IMAGE_SUFFIXES:
        return ContentType.photo
    if suffix in VIDEO_SUFFIXES:
        return ContentType.video
    return ContentType.link


def attachment_url(url: str | None, proxy_url: str | None = None) -> str:
    """Prefer the signed CDN url, fall back to the media-proxy url."""
    return (url or "").strip() or (proxy_url or "").strip()


def build_media_items(
    attachments: Iterable[AttachmentInfo] = (),
    sticker_urls: Iterable[str] = (),
    *,
    start_index: int = 0,
) -> list[MediaItem]:
    """Album order is the message order; index stays stable in the DB."""
    items: list[MediaItem] = []
    index = start_index
    for attachment in attachments:
        url = attachment_url(attachment.url)
        if not url:
            continue
        items.append(
            MediaItem(
                content_type=classify_attachment(
                    attachment.content_type, attachment.filename
                ),
                order_index=index,
                discord_attachment_url=url,
            )
        )
        index += 1
    for url in sticker_urls:
        if not url:
            continue
        items.append(
            MediaItem(
                content_type=ContentType.sticker,
                order_index=index,
                discord_attachment_url=url,
            )
        )
        index += 1
    return items


def extract_links(text: str | None) -> list[str]:
    return URL_RE.findall(text or "")


def has_content(text: str | None, media: Sequence[MediaItem]) -> bool:
    return bool((text or "").strip() or media)


# --- moderator input ---------------------------------------------------------

SCHEDULE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y %H:%M",
)


def parse_schedule_input(value: str | None) -> datetime | None:
    """Parse a moderator-typed publish time; naive input is treated as UTC."""
    text = (value or "").strip().replace("  ", " ")
    if not text:
        return None
    for fmt in SCHEDULE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None
