"""Domain models for the Discord/Telegram suggest bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(str, Enum):
    """Platform a submission came from; also used as an actor platform."""

    telegram = "telegram"
    discord = "discord"


# Admins, blacklist entries and moderation refs live on the same two platforms.
Platform = Source


class SubmissionStatus(str, Enum):
    draft = "draft"
    awaiting_privacy = "awaiting_privacy"
    pending = "pending"
    approved = "approved"
    scheduled = "scheduled"
    published = "published"
    rejected = "rejected"


class ContentType(str, Enum):
    text = "text"
    photo = "photo"
    video = "video"
    sticker = "sticker"
    link = "link"
    album = "album"
    mixed = "mixed"


class RefKind(str, Enum):
    """How a media `file_ref` should be interpreted by an adapter."""

    telegram_file_id = "telegram_file_id"
    discord_url = "discord_url"
    local_path = "local_path"


class PublishTarget(str, Enum):
    """Where an approved submission is published (moderator may override)."""

    telegram = "telegram"
    discord = "discord"
    both = "both"


class MirrorKind(str, Enum):
    suggest_publish = "suggest_publish"
    channel_mirror = "channel_mirror"


@dataclass
class MediaItem:
    content_type: ContentType
    order_index: int = 0
    # Telegram adapters fill file_id; Discord adapters fill url or local path.
    file_id: str | None = None
    discord_attachment_url: str | None = None
    local_path: str | None = None
    caption: str | None = None

    @property
    def file_ref(self) -> str | None:
        return self.file_id or self.discord_attachment_url or self.local_path

    @property
    def ref_kind(self) -> RefKind | None:
        if self.file_id:
            return RefKind.telegram_file_id
        if self.discord_attachment_url:
            return RefKind.discord_url
        if self.local_path:
            return RefKind.local_path
        return None

    @classmethod
    def from_ref(
        cls,
        *,
        content_type: ContentType,
        order_index: int,
        file_ref: str | None,
        ref_kind: RefKind | None,
        caption: str | None = None,
    ) -> MediaItem:
        item = cls(
            content_type=content_type, order_index=order_index, caption=caption
        )
        if file_ref is None or ref_kind is None:
            return item
        if ref_kind is RefKind.telegram_file_id:
            item.file_id = file_ref
        elif ref_kind is RefKind.discord_url:
            item.discord_attachment_url = file_ref
        else:
            item.local_path = file_ref
        return item


@dataclass
class Submission:
    source: Source
    author_platform_user_id: str
    author_display_name: str
    id: int | None = None
    status: SubmissionStatus = SubmissionStatus.draft
    author_username: str | None = None
    author_discord_profile_url: str | None = None
    want_anonymous: bool | None = None
    text: str | None = None
    media: list[MediaItem] = field(default_factory=list)
    is_admin_post: bool = False
    guild_id: str | None = None
    source_chat_id: str | None = None
    source_message_id: str | None = None
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    reject_reason: str | None = None
    publish_target: PublishTarget = PublishTarget.both
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def content_type(self) -> ContentType:
        if not self.media:
            return ContentType.text
        if len(self.media) > 1:
            kinds = {item.content_type for item in self.media}
            return ContentType.album if len(kinds) == 1 else ContentType.mixed
        return self.media[0].content_type


@dataclass
class Admin:
    platform: Platform
    platform_user_id: str
    added_by: str | None = None
    created_at: datetime | None = None


@dataclass
class GuildConfig:
    guild_id: str
    suggest_channel_id: str | None = None
    mod_channel_id: str | None = None
    publish_channel_id: str | None = None
    propose_role_ids: list[str] = field(default_factory=list)
    mod_role_ids: list[str] = field(default_factory=list)
    rate_limit_enabled: bool = False
    rate_limit_count: int | None = None
    rate_limit_window_sec: int | None = None


class PassRequestStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"


@dataclass
class PassRequest:
    """Temporary Discord role grant requested via slash command."""

    guild_id: str
    user_id: str
    display_name: str
    id: int | None = None
    username: str | None = None
    status: PassRequestStatus = PassRequestStatus.pending
    created_at: datetime | None = None
    updated_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    expires_at: datetime | None = None
    cooldown_until: datetime | None = None
    mod_channel_id: str | None = None
    mod_message_id: str | None = None


@dataclass
class MirrorLink:
    """Maps a TG channel message to its Discord twin (and vice versa)."""

    origin: Platform
    kind: MirrorKind
    id: int | None = None
    tg_chat_id: str | None = None
    tg_message_id: str | None = None
    ds_guild_id: str | None = None
    ds_channel_id: str | None = None
    ds_message_id: str | None = None
    submission_id: int | None = None
    created_at: datetime | None = None


@dataclass
class BlacklistEntry:
    platform: Platform
    platform_user_id: str
    reason: str | None = None
    created_at: datetime | None = None


@dataclass
class ModerationRef:
    """Where a moderation card lives: TG admin chat or DS mod channel message."""

    submission_id: int
    platform: Platform
    target_id: str
    message_id: str


# --- domain events -----------------------------------------------------------


@dataclass(frozen=True)
class DomainEvent:
    occurred_at: datetime = field(default_factory=utcnow, kw_only=True)


@dataclass(frozen=True)
class SubmissionCreated(DomainEvent):
    submission: Submission


@dataclass(frozen=True)
class SubmissionUpdated(DomainEvent):
    submission: Submission


@dataclass(frozen=True)
class SubmissionSubmitted(DomainEvent):
    submission: Submission


@dataclass(frozen=True)
class SubmissionApproved(DomainEvent):
    submission: Submission
    moderator_platform: Platform | None = None
    moderator_id: str | None = None


@dataclass(frozen=True)
class SubmissionRejected(DomainEvent):
    submission: Submission
    reason: str | None = None
    moderator_platform: Platform | None = None
    moderator_id: str | None = None


@dataclass(frozen=True)
class SubmissionScheduled(DomainEvent):
    submission: Submission
    scheduled_at: datetime | None = None


@dataclass(frozen=True)
class SubmissionPublished(DomainEvent):
    submission: Submission
    platform: Platform | None = None
    target_id: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class AdminChanged(DomainEvent):
    admin: Admin
    action: str = "added"  # added | removed


@dataclass(frozen=True)
class UserBlocked(DomainEvent):
    entry: BlacklistEntry


@dataclass(frozen=True)
class UserUnblocked(DomainEvent):
    platform: Platform
    platform_user_id: str
