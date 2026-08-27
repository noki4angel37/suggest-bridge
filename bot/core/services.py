"""Application services: submissions, moderation, admins, guards."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from bot.core import rules
from bot.core.db import BridgeDatabase
from bot.core.events import EventBus
from bot.core.models import (
    Admin,
    AdminChanged,
    BlacklistEntry,
    GuildConfig,
    MediaItem,
    ModerationRef,
    Platform,
    PublishTarget,
    Source,
    Submission,
    SubmissionApproved,
    SubmissionCreated,
    SubmissionPublished,
    SubmissionRejected,
    SubmissionScheduled,
    SubmissionStatus,
    SubmissionSubmitted,
    SubmissionUpdated,
    UserBlocked,
    UserUnblocked,
    utcnow,
)

ANTIFLOOD_LIMIT_KEY = "antiflood_limit"
ANTIFLOOD_WINDOW_KEY = "antiflood_window_sec"
DEFAULT_ANTIFLOOD_LIMIT = 5
DEFAULT_ANTIFLOOD_WINDOW_SEC = 60


class SubmissionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ModerationResult:
    """Outcome of a moderation call; `changed` is False for repeated calls."""

    submission: Submission
    changed: bool
    already_handled: bool = False


class SubmissionService:
    def __init__(self, db: BridgeDatabase, bus: EventBus) -> None:
        self.db = db
        self.bus = bus

    def _require(self, submission_id: int) -> Submission:
        submission = self.db.get_submission(submission_id)
        if submission is None:
            raise SubmissionNotFoundError(f"Заявка {submission_id} не найдена")
        return submission

    async def create_draft(
        self,
        *,
        source: Source,
        author_platform_user_id: str,
        author_display_name: str,
        author_username: str | None = None,
        author_discord_profile_url: str | None = None,
        text: str | None = None,
        media: list[MediaItem] | None = None,
        is_admin_post: bool = False,
        guild_id: str | None = None,
        source_chat_id: str | None = None,
        source_message_id: str | None = None,
    ) -> Submission:
        draft = Submission(
            source=source,
            author_platform_user_id=str(author_platform_user_id),
            author_display_name=author_display_name,
            author_username=author_username,
            author_discord_profile_url=author_discord_profile_url,
            status=SubmissionStatus.draft,
            text=rules.validate_text(text) or None,
            media=list(media or []),
            is_admin_post=is_admin_post,
            guild_id=guild_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        stored = self.db.insert_submission(draft)
        await self.bus.publish(SubmissionCreated(submission=stored))
        return stored

    async def update_draft(
        self,
        submission_id: int,
        *,
        text: str | None = None,
        is_admin_post: bool | None = None,
        author_display_name: str | None = None,
        source_message_id: str | None = None,
    ) -> Submission:
        self._require(submission_id)
        fields: dict[str, object] = {}
        if text is not None:
            fields["text"] = rules.validate_text(text) or None
        if is_admin_post is not None:
            fields["is_admin_post"] = is_admin_post
        if author_display_name is not None:
            fields["author_display_name"] = author_display_name
        if source_message_id is not None:
            fields["source_message_id"] = source_message_id
        if fields:
            self.db.update_submission(submission_id, **fields)
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionUpdated(submission=updated))
        return updated

    async def attach_media(
        self, submission_id: int, items: list[MediaItem], *, replace: bool = False
    ) -> Submission:
        self._require(submission_id)
        if replace:
            self.db.replace_media(submission_id, items)
        else:
            self.db.append_media(submission_id, items)
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionUpdated(submission=updated))
        return updated

    async def set_privacy(
        self, submission_id: int, *, want_anonymous: bool
    ) -> Submission:
        self._require(submission_id)
        self.db.update_submission(submission_id, want_anonymous=want_anonymous)
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionUpdated(submission=updated))
        return updated

    async def set_publish_target(
        self, submission_id: int, target: PublishTarget
    ) -> Submission:
        self._require(submission_id)
        self.db.update_submission(submission_id, publish_target=target)
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionUpdated(submission=updated))
        return updated

    async def request_privacy(self, submission_id: int) -> Submission:
        """Move a draft into the "waiting for anonymity choice" state."""
        submission = self._require(submission_id)
        rules.ensure_transition(
            submission.status, SubmissionStatus.awaiting_privacy
        )
        self.db.update_submission(
            submission_id, status=SubmissionStatus.awaiting_privacy
        )
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionUpdated(submission=updated))
        return updated

    async def submit(self, submission_id: int) -> Submission:
        """Send a draft to moderation (draft/awaiting_privacy -> pending)."""
        submission = self._require(submission_id)
        if submission.status is SubmissionStatus.pending:
            return submission
        rules.ensure_transition(submission.status, SubmissionStatus.pending)
        if not (submission.text or submission.media):
            raise ValueError("Заявка пустая: нет текста и медиа")
        from bot.settings import keyword_blocklist

        blocked = rules.text_blocked_by_keywords(
            rules.submission_filter_text(submission), keyword_blocklist()
        )
        if blocked:
            raise ValueError(
                "Текст отклонён фильтром. Уберите запрещённые слова и попробуйте снова."
            )
        self.db.update_submission(submission_id, status=SubmissionStatus.pending)
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionSubmitted(submission=updated))
        return updated

    def get(self, submission_id: int) -> Submission | None:
        return self.db.get_submission(submission_id)

    def list_pending(self, *, limit: int = 50) -> list[Submission]:
        return self.db.list_submissions(
            status=SubmissionStatus.pending, limit=limit, order_by="id"
        )

    def list_scheduled(self, *, limit: int = 50) -> list[Submission]:
        return self.db.list_submissions(
            status=SubmissionStatus.scheduled,
            limit=limit,
            order_by="scheduled_at",
        )

    def list_by_status(
        self, status: SubmissionStatus, *, limit: int = 50
    ) -> list[Submission]:
        order = "scheduled_at" if status is SubmissionStatus.scheduled else "id"
        return self.db.list_submissions(status=status, limit=limit, order_by=order)

    async def cancel_draft(self, submission_id: int) -> bool:
        """Delete a draft (and its media files). Returns False if not a draft."""
        from bot.core.media_store import delete_submission_files

        submission = self.db.get_submission(submission_id)
        if submission is None:
            return False
        if submission.status not in (
            SubmissionStatus.draft,
            SubmissionStatus.awaiting_privacy,
        ):
            raise ValueError(
                f"Отменить можно только черновик, сейчас: {submission.status.value}"
            )
        db_path = getattr(self.db, "db_path", None)
        delete_submission_files(submission_id, db_path=db_path)
        return self.db.delete_submission(submission_id)

    async def edit_moderator_text(
        self, submission_id: int, text: str | None
    ) -> Submission:
        """Change body text before publish (pending / scheduled / approved)."""
        submission = self._require(submission_id)
        if submission.status not in (
            SubmissionStatus.pending,
            SubmissionStatus.scheduled,
            SubmissionStatus.approved,
        ):
            raise ValueError(
                "Текст можно править только до публикации "
                f"(сейчас: {submission.status.value})"
            )
        self.db.update_submission(
            submission_id, text=rules.validate_text(text) or None
        )
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionUpdated(submission=updated))
        return updated

    async def cache_discord_media(self, submission_id: int) -> Submission:
        """Download Discord CDN attachments into local media store."""
        from bot.core.media_store import materialize_discord_media

        submission = self._require(submission_id)
        if not submission.media:
            return submission
        db_path = getattr(self.db, "db_path", None)
        cached = await materialize_discord_media(
            submission_id, submission.media, db_path=db_path
        )
        if cached == submission.media:
            return submission
        self.db.replace_media(submission_id, cached)
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionUpdated(submission=updated))
        return updated

    async def cache_discord_blobs(
        self, submission_id: int, blobs: dict[int, bytes]
    ) -> Submission:
        """Write already-read Discord attachment bytes into the media store."""
        from bot.core.media_store import materialize_from_blobs

        submission = self._require(submission_id)
        if not submission.media or not blobs:
            return submission
        db_path = getattr(self.db, "db_path", None)
        cached = materialize_from_blobs(
            submission_id, submission.media, blobs, db_path=db_path
        )
        self.db.replace_media(submission_id, cached)
        updated = self._require(submission_id)
        await self.bus.publish(SubmissionUpdated(submission=updated))
        return updated


class ModerationService:
    def __init__(self, db: BridgeDatabase, bus: EventBus) -> None:
        self.db = db
        self.bus = bus

    def _require(self, submission_id: int) -> Submission:
        submission = self.db.get_submission(submission_id)
        if submission is None:
            raise SubmissionNotFoundError(f"Заявка {submission_id} не найдена")
        return submission

    async def approve(
        self,
        submission_id: int,
        *,
        moderator_platform: Platform | None = None,
        moderator_id: str | None = None,
        scheduled_at: datetime | None = None,
    ) -> ModerationResult:
        submission = self._require(submission_id)
        if rules.is_handled(submission.status):
            return ModerationResult(
                submission=submission, changed=False, already_handled=True
            )

        target = (
            SubmissionStatus.scheduled
            if scheduled_at is not None
            else SubmissionStatus.approved
        )
        rules.ensure_transition(submission.status, target)
        ok = self.db.update_submission_cas(
            submission_id,
            submission.status,
            status=target,
            scheduled_at=scheduled_at,
        )
        if not ok:
            current = self._require(submission_id)
            return ModerationResult(
                submission=current, changed=False, already_handled=True
            )
        updated = self._require(submission_id)
        await self.bus.publish(
            SubmissionApproved(
                submission=updated,
                moderator_platform=moderator_platform,
                moderator_id=moderator_id,
            )
        )
        if target is SubmissionStatus.scheduled:
            await self.bus.publish(
                SubmissionScheduled(
                    submission=updated, scheduled_at=scheduled_at
                )
            )
        return ModerationResult(submission=updated, changed=True)

    async def reject(
        self,
        submission_id: int,
        *,
        reason: str | None = None,
        moderator_platform: Platform | None = None,
        moderator_id: str | None = None,
    ) -> ModerationResult:
        submission = self._require(submission_id)
        if rules.is_handled(submission.status):
            return ModerationResult(
                submission=submission, changed=False, already_handled=True
            )
        rules.ensure_transition(submission.status, SubmissionStatus.rejected)
        ok = self.db.update_submission_cas(
            submission_id,
            submission.status,
            status=SubmissionStatus.rejected,
            reject_reason=reason,
        )
        if not ok:
            current = self._require(submission_id)
            return ModerationResult(
                submission=current, changed=False, already_handled=True
            )
        updated = self._require(submission_id)
        await self.bus.publish(
            SubmissionRejected(
                submission=updated,
                reason=reason,
                moderator_platform=moderator_platform,
                moderator_id=moderator_id,
            )
        )
        return ModerationResult(submission=updated, changed=True)

    async def schedule(
        self, submission_id: int, at: datetime
    ) -> ModerationResult:
        submission = self._require(submission_id)
        if submission.status in (
            SubmissionStatus.published,
            SubmissionStatus.rejected,
        ):
            return ModerationResult(
                submission=submission, changed=False, already_handled=True
            )
        rules.ensure_transition(submission.status, SubmissionStatus.scheduled)
        ok = self.db.update_submission_cas(
            submission_id,
            submission.status,
            status=SubmissionStatus.scheduled,
            scheduled_at=at,
        )
        if not ok:
            current = self._require(submission_id)
            return ModerationResult(
                submission=current, changed=False, already_handled=True
            )
        updated = self._require(submission_id)
        await self.bus.publish(
            SubmissionScheduled(submission=updated, scheduled_at=at)
        )
        return ModerationResult(submission=updated, changed=True)

    async def mark_published(
        self,
        submission_id: int,
        *,
        platform: Platform | None = None,
        target_id: str | None = None,
        message_id: str | None = None,
        published_at: datetime | None = None,
    ) -> ModerationResult:
        submission = self._require(submission_id)
        if submission.status is SubmissionStatus.published:
            return ModerationResult(
                submission=submission, changed=False, already_handled=True
            )
        rules.ensure_transition(submission.status, SubmissionStatus.published)
        ok = self.db.update_submission_cas(
            submission_id,
            (SubmissionStatus.approved, SubmissionStatus.scheduled),
            status=SubmissionStatus.published,
            published_at=published_at or utcnow(),
        )
        if not ok:
            current = self._require(submission_id)
            if current.status is SubmissionStatus.published:
                return ModerationResult(
                    submission=current, changed=False, already_handled=True
                )
            raise RuntimeError(
                f"Не удалось отметить заявку {submission_id} опубликованной "
                f"(статус {current.status.value})"
            )
        updated = self._require(submission_id)
        await self.bus.publish(
            SubmissionPublished(
                submission=updated,
                platform=platform,
                target_id=target_id,
                message_id=message_id,
            )
        )
        return ModerationResult(submission=updated, changed=True)

    def save_moderation_ref(
        self,
        submission_id: int,
        *,
        platform: Platform,
        target_id: str,
        message_id: str,
    ) -> ModerationRef:
        ref = ModerationRef(
            submission_id=submission_id,
            platform=platform,
            target_id=str(target_id),
            message_id=str(message_id),
        )
        self.db.save_moderation_ref(ref)
        return ref

    def get_moderation_refs(
        self, submission_id: int, *, platform: Platform | None = None
    ) -> list[ModerationRef]:
        return self.db.get_moderation_refs(submission_id, platform=platform)


class AdminService:
    def __init__(self, db: BridgeDatabase, bus: EventBus) -> None:
        self.db = db
        self.bus = bus

    def bootstrap_telegram_admins(
        self, admin_ids: Iterable[int | str] | None
    ) -> list[Admin]:
        """Seed Telegram admins from config (ADMIN_IDS) without events."""
        seeded: list[Admin] = []
        for raw in admin_ids or ():
            user_id = str(raw).strip()
            if not user_id:
                continue
            seeded.append(
                self.db.upsert_admin(
                    Platform.telegram, user_id, added_by="bootstrap"
                )
            )
        return seeded

    def bootstrap_owner_admins(self) -> list[Admin]:
        """Seed OWNER_TELEGRAM_ID / OWNER_DISCORD_ID into the admins table."""
        from bot.core.host_control import owner_discord_id, owner_telegram_id

        seeded: list[Admin] = []
        tg = owner_telegram_id()
        if tg:
            seeded.append(
                self.db.upsert_admin(
                    Platform.telegram, tg, added_by="owner_bootstrap"
                )
            )
        ds = owner_discord_id()
        if ds:
            seeded.append(
                self.db.upsert_admin(
                    Platform.discord, ds, added_by="owner_bootstrap"
                )
            )
        return seeded

    async def add_admin(
        self,
        platform: Platform,
        platform_user_id: str,
        *,
        added_by: str | None = None,
    ) -> Admin:
        admin = self.db.upsert_admin(
            platform, str(platform_user_id), added_by=added_by
        )
        await self.bus.publish(AdminChanged(admin=admin, action="added"))
        return admin

    async def remove_admin(
        self, platform: Platform, platform_user_id: str
    ) -> bool:
        user_id = str(platform_user_id)
        existing = self.db.get_admin(platform, user_id)
        removed = self.db.delete_admin(platform, user_id)
        if removed and existing is not None:
            await self.bus.publish(
                AdminChanged(admin=existing, action="removed")
            )
        return removed

    def is_admin(self, platform: Platform, platform_user_id: str) -> bool:
        return self.db.get_admin(platform, str(platform_user_id)) is not None

    def is_owner(self, platform: Platform, platform_user_id: str) -> bool:
        """Super-admin from OWNER_TELEGRAM_ID / OWNER_DISCORD_ID env."""
        from bot.core.host_control import is_owner_discord, is_owner_telegram

        user_id = str(platform_user_id)
        if platform is Platform.telegram:
            return is_owner_telegram(user_id)
        if platform is Platform.discord:
            return is_owner_discord(user_id)
        return False

    def can_manage(self, platform: Platform, platform_user_id: str) -> bool:
        """Admin-table entry or OWNER_* — for command gates."""
        user_id = str(platform_user_id)
        return self.is_admin(platform, user_id) or self.is_owner(platform, user_id)

    def list_admins(self, *, platform: Platform | None = None) -> list[Admin]:
        return self.db.list_admins(platform=platform)


class GuildConfigService:
    def __init__(self, db: BridgeDatabase) -> None:
        self.db = db

    def get(self, guild_id: str) -> GuildConfig | None:
        return self.db.get_guild_config(str(guild_id))

    def get_or_default(self, guild_id: str) -> GuildConfig:
        return self.get(guild_id) or GuildConfig(guild_id=str(guild_id))

    def upsert(self, config: GuildConfig) -> GuildConfig:
        return self.db.upsert_guild_config(config)

    def set_channels(
        self,
        guild_id: str,
        *,
        suggest_channel_id: str | None = None,
        mod_channel_id: str | None = None,
        publish_channel_id: str | None = None,
    ) -> GuildConfig:
        config = self.get_or_default(guild_id)
        if suggest_channel_id is not None:
            config.suggest_channel_id = str(suggest_channel_id)
        if mod_channel_id is not None:
            config.mod_channel_id = str(mod_channel_id)
        if publish_channel_id is not None:
            config.publish_channel_id = str(publish_channel_id)
        return self.upsert(config)

    def list_all(self) -> list[GuildConfig]:
        return self.db.list_guild_configs()

    def set_roles(
        self,
        guild_id: str,
        *,
        propose_role_ids: list[str] | None = None,
        mod_role_ids: list[str] | None = None,
        admin_role_ids: list[str] | None = None,
    ) -> GuildConfig:
        config = self.get_or_default(guild_id)
        if propose_role_ids is not None:
            config.propose_role_ids = [str(x) for x in propose_role_ids]
        if mod_role_ids is not None:
            config.mod_role_ids = [str(x) for x in mod_role_ids]
        if admin_role_ids is not None:
            config.admin_role_ids = [str(x) for x in admin_role_ids]
        return self.upsert(config)

    def set_rate_limit(
        self,
        guild_id: str,
        *,
        enabled: bool,
        count: int | None = None,
        window_sec: int | None = None,
    ) -> GuildConfig:
        config = self.get_or_default(guild_id)
        config.rate_limit_enabled = enabled
        if count is not None:
            config.rate_limit_count = count
        if window_sec is not None:
            config.rate_limit_window_sec = window_sec
        return self.upsert(config)

    def can_propose(self, guild_id: str, role_ids: list[str]) -> bool:
        config = self.get(guild_id)
        if config is None or not config.propose_role_ids:
            return True
        allowed = set(config.propose_role_ids)
        return any(str(role) in allowed for role in role_ids)

    def can_moderate(self, guild_id: str, role_ids: list[str]) -> bool:
        config = self.get(guild_id)
        if config is None or not config.mod_role_ids:
            return False
        allowed = set(config.mod_role_ids)
        return any(str(role) in allowed for role in role_ids)


class BlacklistService:
    def __init__(self, db: BridgeDatabase, bus: EventBus) -> None:
        self.db = db
        self.bus = bus

    async def block(
        self,
        platform: Platform,
        platform_user_id: str,
        *,
        reason: str | None = None,
    ) -> BlacklistEntry:
        entry = self.db.upsert_blacklist(
            platform, str(platform_user_id), reason=reason
        )
        await self.bus.publish(UserBlocked(entry=entry))
        return entry

    async def unblock(
        self, platform: Platform, platform_user_id: str
    ) -> bool:
        user_id = str(platform_user_id)
        removed = self.db.delete_blacklist(platform, user_id)
        if removed:
            await self.bus.publish(
                UserUnblocked(platform=platform, platform_user_id=user_id)
            )
        return removed

    def is_blocked(self, platform: Platform, platform_user_id: str) -> bool:
        return (
            self.db.get_blacklist_entry(platform, str(platform_user_id))
            is not None
        )

    def list_blocked(
        self, *, platform: Platform | None = None
    ) -> list[BlacklistEntry]:
        return self.db.list_blacklist(platform=platform)


@dataclass(frozen=True)
class AntifloodDecision:
    allowed: bool
    count: int
    limit: int
    window_sec: int


class AntifloodService:
    """Always-on burst guard; optional per-guild rate limit overrides numbers."""

    def __init__(
        self,
        db: BridgeDatabase,
        *,
        default_limit: int = DEFAULT_ANTIFLOOD_LIMIT,
        default_window_sec: int = DEFAULT_ANTIFLOOD_WINDOW_SEC,
    ) -> None:
        self.db = db
        self.default_limit = default_limit
        self.default_window_sec = default_window_sec

    @property
    def limit(self) -> int:
        return self.db.get_int_setting(ANTIFLOOD_LIMIT_KEY, self.default_limit)

    @property
    def window_sec(self) -> int:
        return self.db.get_int_setting(
            ANTIFLOOD_WINDOW_KEY, self.default_window_sec
        )

    def check_and_hit(
        self,
        platform: Platform,
        platform_user_id: str,
        *,
        limit: int | None = None,
        window_sec: int | None = None,
        now: float | None = None,
    ) -> bool:
        return self.decide(
            platform,
            platform_user_id,
            limit=limit,
            window_sec=window_sec,
            now=now,
        ).allowed

    def decide(
        self,
        platform: Platform,
        platform_user_id: str,
        *,
        limit: int | None = None,
        window_sec: int | None = None,
        now: float | None = None,
    ) -> AntifloodDecision:
        effective_limit = limit if limit is not None else self.limit
        effective_window = (
            window_sec if window_sec is not None else self.window_sec
        )
        count = self.db.bump_antiflood(
            platform,
            str(platform_user_id),
            now=now if now is not None else time.time(),
            window_sec=effective_window,
        )
        return AntifloodDecision(
            allowed=count <= effective_limit,
            count=count,
            limit=effective_limit,
            window_sec=effective_window,
        )

    def decide_for_guild(
        self,
        platform: Platform,
        platform_user_id: str,
        *,
        guild_config: GuildConfig | None,
        now: float | None = None,
    ) -> AntifloodDecision:
        """Guild rate limit (feature flag, off by default) wins over defaults."""
        limit: int | None = None
        window_sec: int | None = None
        if guild_config is not None and guild_config.rate_limit_enabled:
            limit = guild_config.rate_limit_count
            window_sec = guild_config.rate_limit_window_sec
        return self.decide(
            platform,
            platform_user_id,
            limit=limit,
            window_sec=window_sec,
            now=now,
        )

    def reset(self, platform: Platform, platform_user_id: str) -> None:
        self.db.reset_antiflood(platform, str(platform_user_id))

    def configure_defaults(
        self, *, limit: int | None = None, window_sec: int | None = None
    ) -> None:
        if limit is not None:
            self.db.set_setting(ANTIFLOOD_LIMIT_KEY, str(limit))
        if window_sec is not None:
            self.db.set_setting(ANTIFLOOD_WINDOW_KEY, str(window_sec))
