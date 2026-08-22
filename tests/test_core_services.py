from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.core.db import BridgeDatabase
from bot.core.events import EventBus
from bot.core.models import (
    ContentType,
    DomainEvent,
    GuildConfig,
    MediaItem,
    Platform,
    Source,
    SubmissionApproved,
    SubmissionStatus,
    SubmissionSubmitted,
    utcnow,
)
from bot.core.services import (
    AdminService,
    AntifloodService,
    BlacklistService,
    GuildConfigService,
    ModerationService,
    SubmissionService,
)


@pytest.fixture()
def db(tmp_path: Path) -> BridgeDatabase:
    return BridgeDatabase(str(tmp_path / "bridge.db"))


@pytest.fixture()
def bus() -> EventBus:
    return EventBus()


@pytest.fixture()
def submissions(db: BridgeDatabase, bus: EventBus) -> SubmissionService:
    return SubmissionService(db, bus)


@pytest.fixture()
def moderation(db: BridgeDatabase, bus: EventBus) -> ModerationService:
    return ModerationService(db, bus)


def make_pending(service: SubmissionService, **kwargs: object) -> int:
    defaults: dict[str, object] = {
        "source": Source.telegram,
        "author_platform_user_id": "777",
        "author_display_name": "Пётр",
        "text": "Идея",
    }
    defaults.update(kwargs)
    draft = asyncio.run(service.create_draft(**defaults))  # type: ignore[arg-type]
    assert draft.id is not None
    asyncio.run(service.submit(draft.id))
    return draft.id


def test_create_draft_and_submit_flow(submissions: SubmissionService) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.discord,
            author_platform_user_id="42",
            author_display_name="Ivan",
            author_discord_profile_url="https://discord.com/users/42",
            text="  Предложение  ",
            guild_id="900",
        )
    )
    assert draft.id is not None
    assert draft.status is SubmissionStatus.draft
    assert draft.text == "Предложение"

    submitted = asyncio.run(submissions.submit(draft.id))
    assert submitted.status is SubmissionStatus.pending
    assert [s.id for s in submissions.list_pending()] == [draft.id]


def test_create_draft_enforces_text_limit(
    submissions: SubmissionService,
) -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            submissions.create_draft(
                source=Source.telegram,
                author_platform_user_id="1",
                author_display_name="Пётр",
                text="я" * 401,
            )
        )


def test_submit_requires_content(submissions: SubmissionService) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.telegram,
            author_platform_user_id="1",
            author_display_name="Пётр",
        )
    )
    assert draft.id is not None
    with pytest.raises(ValueError):
        asyncio.run(submissions.submit(draft.id))


def test_set_privacy_and_update_draft(submissions: SubmissionService) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.telegram,
            author_platform_user_id="1",
            author_display_name="Пётр",
            text="Первый вариант",
        )
    )
    assert draft.id is not None
    updated = asyncio.run(
        submissions.update_draft(draft.id, text="Второй вариант")
    )
    assert updated.text == "Второй вариант"

    private = asyncio.run(
        submissions.set_privacy(draft.id, want_anonymous=True)
    )
    assert private.want_anonymous is True


def test_attach_media_keeps_album_order(
    submissions: SubmissionService,
) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.telegram,
            author_platform_user_id="1",
            author_display_name="Пётр",
            text="Альбом",
        )
    )
    assert draft.id is not None
    asyncio.run(
        submissions.attach_media(
            draft.id,
            [MediaItem(content_type=ContentType.photo, file_id="file-1")],
        )
    )
    stored = asyncio.run(
        submissions.attach_media(
            draft.id,
            [MediaItem(content_type=ContentType.photo, file_id="file-2")],
        )
    )
    assert [item.file_ref for item in stored.media] == ["file-1", "file-2"]
    assert [item.order_index for item in stored.media] == [0, 1]
    assert stored.content_type is ContentType.album


def test_submit_publishes_event(
    submissions: SubmissionService, bus: EventBus
) -> None:
    seen: list[DomainEvent] = []

    async def handler(event: SubmissionSubmitted) -> None:
        seen.append(event)

    bus.subscribe(SubmissionSubmitted, handler)
    make_pending(submissions)
    assert len(seen) == 1


def test_event_bus_survives_handler_errors(bus: EventBus) -> None:
    calls: list[str] = []

    async def bad(event: DomainEvent) -> None:
        raise RuntimeError("boom")

    async def good(event: DomainEvent) -> None:
        calls.append("good")

    bus.subscribe(DomainEvent, bad)
    bus.subscribe(DomainEvent, good)
    asyncio.run(bus.publish(SubmissionApproved(submission=None)))  # type: ignore[arg-type]
    assert calls == ["good"]


def test_approve_is_idempotent(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    submission_id = make_pending(submissions)

    first = asyncio.run(
        moderation.approve(
            submission_id,
            moderator_platform=Platform.telegram,
            moderator_id="1",
        )
    )
    assert first.changed is True
    assert first.already_handled is False
    assert first.submission.status is SubmissionStatus.approved

    second = asyncio.run(moderation.approve(submission_id))
    assert second.changed is False
    assert second.already_handled is True
    assert second.submission.status is SubmissionStatus.approved


def test_reject_after_approve_is_noop(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    submission_id = make_pending(submissions)
    asyncio.run(moderation.approve(submission_id))
    result = asyncio.run(moderation.reject(submission_id, reason="Поздно"))
    assert result.already_handled is True
    assert result.submission.status is SubmissionStatus.approved
    assert result.submission.reject_reason is None


def test_reject_stores_reason(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    submission_id = make_pending(submissions)
    result = asyncio.run(
        moderation.reject(submission_id, reason="Не по теме")
    )
    assert result.submission.status is SubmissionStatus.rejected
    assert result.submission.reject_reason == "Не по теме"


def test_schedule_then_publish(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    submission_id = make_pending(submissions)
    at = utcnow()
    scheduled = asyncio.run(moderation.schedule(submission_id, at))
    assert scheduled.submission.status is SubmissionStatus.scheduled
    assert scheduled.submission.scheduled_at is not None

    published = asyncio.run(
        moderation.mark_published(
            submission_id,
            platform=Platform.telegram,
            target_id="-100500",
            message_id="12",
        )
    )
    assert published.submission.status is SubmissionStatus.published
    assert asyncio.run(moderation.mark_published(submission_id)).changed is False


def test_moderation_refs_roundtrip(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    submission_id = make_pending(submissions)
    moderation.save_moderation_ref(
        submission_id,
        platform=Platform.telegram,
        target_id="1",
        message_id="10",
    )
    moderation.save_moderation_ref(
        submission_id,
        platform=Platform.discord,
        target_id="chan",
        message_id="20",
    )
    refs = moderation.get_moderation_refs(submission_id)
    assert len(refs) == 2
    discord_refs = moderation.get_moderation_refs(
        submission_id, platform=Platform.discord
    )
    assert [r.message_id for r in discord_refs] == ["20"]


def test_admin_service_bootstrap_and_changes(
    db: BridgeDatabase, bus: EventBus
) -> None:
    admins = AdminService(db, bus)
    admins.bootstrap_telegram_admins([111, "222"])
    assert admins.is_admin(Platform.telegram, "111")
    assert admins.is_admin(Platform.telegram, 222)  # type: ignore[arg-type]
    assert not admins.is_admin(Platform.discord, "111")

    asyncio.run(
        admins.add_admin(Platform.discord, "333", added_by="tg:111")
    )
    assert admins.is_admin(Platform.discord, "333")
    assert len(admins.list_admins()) == 3

    assert asyncio.run(admins.remove_admin(Platform.discord, "333")) is True
    assert asyncio.run(admins.remove_admin(Platform.discord, "333")) is False
    assert not admins.is_admin(Platform.discord, "333")


def test_admin_owner_can_manage_and_bootstrap(
    db: BridgeDatabase, bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "9001")
    monkeypatch.setenv("OWNER_DISCORD_ID", "9002")
    admins = AdminService(db, bus)
    assert admins.can_manage(Platform.telegram, "9001")
    assert admins.can_manage(Platform.discord, "9002")
    assert not admins.is_admin(Platform.discord, "9002")
    seeded = admins.bootstrap_owner_admins()
    assert len(seeded) == 2
    assert admins.is_admin(Platform.telegram, "9001")
    assert admins.is_admin(Platform.discord, "9002")
    assert admins.can_manage(Platform.discord, "9002")
    assert not admins.can_manage(Platform.discord, "1")


def test_blacklist_block_and_unblock(
    db: BridgeDatabase, bus: EventBus
) -> None:
    blacklist = BlacklistService(db, bus)
    assert blacklist.is_blocked(Platform.discord, "55") is False

    entry = asyncio.run(
        blacklist.block(Platform.discord, "55", reason="Спам")
    )
    assert entry.reason == "Спам"
    assert blacklist.is_blocked(Platform.discord, "55") is True
    assert blacklist.is_blocked(Platform.telegram, "55") is False
    assert len(blacklist.list_blocked()) == 1

    assert asyncio.run(blacklist.unblock(Platform.discord, "55")) is True
    assert asyncio.run(blacklist.unblock(Platform.discord, "55")) is False
    assert blacklist.is_blocked(Platform.discord, "55") is False


def test_antiflood_blocks_burst(db: BridgeDatabase) -> None:
    antiflood = AntifloodService(db, default_limit=3, default_window_sec=60)
    now = 1_000.0
    allowed = [
        antiflood.check_and_hit(Platform.telegram, "9", now=now + i * 0.1)
        for i in range(4)
    ]
    assert allowed == [True, True, True, False]


def test_antiflood_window_resets(db: BridgeDatabase) -> None:
    antiflood = AntifloodService(db, default_limit=2, default_window_sec=60)
    now = 5_000.0
    assert antiflood.check_and_hit(Platform.telegram, "9", now=now) is True
    assert antiflood.check_and_hit(Platform.telegram, "9", now=now) is True
    assert antiflood.check_and_hit(Platform.telegram, "9", now=now) is False
    assert (
        antiflood.check_and_hit(Platform.telegram, "9", now=now + 61) is True
    )


def test_antiflood_is_per_user_and_platform(db: BridgeDatabase) -> None:
    antiflood = AntifloodService(db, default_limit=1, default_window_sec=60)
    now = 10.0
    assert antiflood.check_and_hit(Platform.telegram, "1", now=now) is True
    assert antiflood.check_and_hit(Platform.telegram, "1", now=now) is False
    assert antiflood.check_and_hit(Platform.telegram, "2", now=now) is True
    assert antiflood.check_and_hit(Platform.discord, "1", now=now) is True


def test_antiflood_defaults_from_settings(db: BridgeDatabase) -> None:
    antiflood = AntifloodService(db, default_limit=5, default_window_sec=60)
    antiflood.configure_defaults(limit=1, window_sec=30)
    assert antiflood.limit == 1
    assert antiflood.window_sec == 30
    now = 0.0
    assert antiflood.check_and_hit(Platform.discord, "7", now=now) is True
    assert antiflood.check_and_hit(Platform.discord, "7", now=now) is False


def test_guild_rate_limit_overrides_defaults(db: BridgeDatabase) -> None:
    guilds = GuildConfigService(db)
    antiflood = AntifloodService(db, default_limit=10, default_window_sec=60)
    config = guilds.set_rate_limit(
        "900", enabled=True, count=1, window_sec=60
    )
    assert config.rate_limit_enabled is True

    now = 100.0
    first = antiflood.decide_for_guild(
        Platform.discord, "8", guild_config=config, now=now
    )
    second = antiflood.decide_for_guild(
        Platform.discord, "8", guild_config=config, now=now
    )
    assert (first.allowed, second.allowed) == (True, False)
    assert first.limit == 1


def test_guild_config_roles(db: BridgeDatabase) -> None:
    guilds = GuildConfigService(db)
    guilds.upsert(
        GuildConfig(
            guild_id="900",
            suggest_channel_id="1",
            mod_channel_id="2",
            propose_role_ids=["10", "11"],
            mod_role_ids=["20"],
        )
    )
    stored = guilds.get("900")
    assert stored is not None
    assert stored.propose_role_ids == ["10", "11"]
    assert stored.mod_role_ids == ["20"]
    assert guilds.can_propose("900", ["11"]) is True
    assert guilds.can_propose("900", ["99"]) is False
    assert guilds.can_moderate("900", ["20"]) is True
    assert guilds.can_moderate("900", ["10"]) is False
    # Unknown guild: proposing is open, moderating is not.
    assert guilds.can_propose("901", []) is True
    assert guilds.can_moderate("901", ["20"]) is False


def test_guild_config_channels_upsert(db: BridgeDatabase) -> None:
    guilds = GuildConfigService(db)
    guilds.set_channels("900", suggest_channel_id="111")
    updated = guilds.set_channels("900", mod_channel_id="222")
    assert updated.suggest_channel_id == "111"
    assert updated.mod_channel_id == "222"
