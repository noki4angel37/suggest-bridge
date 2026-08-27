"""Platform-agnostic publish plan: what goes to the channel and in which shape.

Adapters (aiogram / discord.py) take a :class:`PublishPlan` and translate it into
platform API calls; all caption and layout decisions stay here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from bot.core.models import ContentType, MediaItem, RefKind, Source, Submission
from bot.core.rules import build_channel_caption

# Only these types can travel inside one album (Telegram media group).
GROUPABLE_TYPES = frozenset({ContentType.photo, ContentType.video})
# Stickers are sent bare: no platform lets us attach a caption to them.
CAPTIONLESS_TYPES = frozenset({ContentType.sticker})
# Telegram caption limit; a longer body has to go as a separate message.
CAPTION_LIMIT = 1024


class PublishMode(str, Enum):
    text = "text"
    single = "single"
    album = "album"


@dataclass(frozen=True)
class PublishMedia:
    """One attachment resolved to a ref an adapter knows how to send."""

    content_type: ContentType
    file_ref: str
    ref_kind: RefKind
    order_index: int = 0
    caption: str | None = None

    @property
    def needs_download(self) -> bool:
        """Discord CDN links must be re-uploaded, not forwarded as a ref."""
        return self.ref_kind is RefKind.discord_url

    @property
    def is_groupable(self) -> bool:
        return self.content_type in GROUPABLE_TYPES

    @property
    def accepts_caption(self) -> bool:
        return self.content_type not in CAPTIONLESS_TYPES


@dataclass(frozen=True)
class PublishPlan:
    """Everything an adapter needs to publish one submission."""

    caption: str
    mode: PublishMode
    media: tuple[PublishMedia, ...] = ()
    submission_id: int | None = None
    source: Source = Source.telegram
    with_author: bool = False
    is_admin_post: bool = False
    # True when no attachment can carry the caption (stickers, oversized body).
    caption_as_separate_message: bool = False

    @property
    def needs_download(self) -> bool:
        return any(item.needs_download for item in self.media)

    @property
    def album_items(self) -> tuple[PublishMedia, ...]:
        if self.mode is not PublishMode.album:
            return ()
        return tuple(item for item in self.media if item.is_groupable)

    @property
    def standalone_items(self) -> tuple[PublishMedia, ...]:
        """Album leftovers (e.g. stickers) that must be sent one by one."""
        if self.mode is not PublishMode.album:
            return self.media
        return tuple(item for item in self.media if not item.is_groupable)


def resolve_with_author(submission: Submission) -> bool:
    """Anonymous unless the author (or a moderator) explicitly asked otherwise."""
    return submission.want_anonymous is False


def build_publish_plan(
    submission: Submission,
    *,
    with_author: bool | None = None,
    author_line_override: str | None = None,
) -> PublishPlan:
    """Build the channel post plan for a submission.

    Caption rules come from :func:`bot.core.rules.build_channel_caption`:
    subscriber posts carry the hashtag, admin posts never do, and Discord-origin
    posts get a source line with the platform name.
    """
    named = resolve_with_author(submission) if with_author is None else with_author
    caption = _with_links(
        build_channel_caption(
            submission,
            with_author=named,
            author_line_override=author_line_override,
        ),
        submission.media,
    )

    media = _collect_media(submission.media)
    if not media:
        mode = PublishMode.text
    elif len(media) == 1:
        mode = PublishMode.single
    else:
        mode = PublishMode.album
    media, caption_separate = _place_caption(mode, media, caption)

    return PublishPlan(
        caption=caption,
        mode=mode,
        media=media,
        submission_id=submission.id,
        source=submission.source,
        with_author=named,
        is_admin_post=submission.is_admin_post,
        caption_as_separate_message=caption_separate,
    )


def extract_publish_ref(result: object) -> tuple[str | None, str | None]:
    """Pull (target_id, message_id) out of whatever a publish callback returned."""
    target_id, message_id, _ = extract_publish_refs(result)
    return target_id, message_id


def extract_publish_refs(
    result: object,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Pull (target_id, primary_message_id, all_message_ids) from publish result."""
    if result is None:
        return None, None, ()
    if isinstance(result, tuple) and len(result) == 2:
        target, message = result
        mid = _as_str(message)
        return _as_str(target), mid, (mid,) if mid else ()
    target = getattr(result, "target_id", None)
    if target is None:
        target = getattr(result, "chat_id", None)
    all_ids = getattr(result, "message_ids", None)
    if all_ids:
        ids = tuple(str(x) for x in all_ids if x is not None)
        primary = ids[0] if ids else None
        return _as_str(target), primary, ids
    mid = _as_str(getattr(result, "message_id", None))
    return _as_str(target), mid, (mid,) if mid else ()


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _with_links(caption: str, items: list[MediaItem]) -> str:
    """Bare links (stored as media without a ref) belong in the post body."""
    extra = [
        item.caption
        for item in items
        if item.content_type is ContentType.link
        and not item.file_ref
        and item.caption
        and item.caption not in caption
    ]
    if not extra:
        return caption
    return "\n".join([*extra, "", caption]) if caption else "\n".join(extra)


def _collect_media(items: list[MediaItem]) -> tuple[PublishMedia, ...]:
    resolved: list[PublishMedia] = []
    for item in sorted(items, key=lambda media: media.order_index):
        file_ref = item.file_ref
        ref_kind = item.ref_kind
        if not file_ref or ref_kind is None:
            continue
        resolved.append(
            PublishMedia(
                content_type=item.content_type,
                file_ref=file_ref,
                ref_kind=ref_kind,
                order_index=item.order_index,
            )
        )
    return tuple(resolved)


def _place_caption(
    mode: PublishMode, media: tuple[PublishMedia, ...], caption: str
) -> tuple[tuple[PublishMedia, ...], bool]:
    """Attach the caption to the first item able to carry it.

    Returns the media tuple plus a flag telling the adapter to send the caption
    as its own message instead.
    """
    if mode is PublishMode.text or not media:
        return media, False
    if len(caption) > CAPTION_LIMIT:
        return media, True

    carrier = next(
        (
            index
            for index, item in enumerate(media)
            if item.accepts_caption
            and (mode is PublishMode.single or item.is_groupable)
        ),
        None,
    )
    if carrier is None:
        return media, True
    updated = list(media)
    updated[carrier] = replace(media[carrier], caption=caption)
    return tuple(updated), False
