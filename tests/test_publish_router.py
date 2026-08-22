"""Tests for dual-publish PublishRouter and mirror settings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.core.db import BridgeDatabase
from bot.core.models import (
    MirrorKind,
    MirrorLink,
    Platform,
    PublishTarget,
    Source,
    Submission,
    SubmissionStatus,
)
from bot.core.publish_router import (
    PublishRouter,
    PublishRouterError,
    is_mirror_enabled,
    set_mirror_enabled,
)


def _submission(
    tmp_db: BridgeDatabase, *, target: PublishTarget = PublishTarget.both
) -> Submission:
    stored = tmp_db.insert_submission(
        Submission(
            source=Source.telegram,
            author_platform_user_id="1",
            author_display_name="Test",
            status=SubmissionStatus.approved,
            text="hello",
            publish_target=target,
            guild_id="g1",
        )
    )
    assert stored.id is not None
    return stored


@pytest.fixture
def db(tmp_path: Path) -> BridgeDatabase:
    return BridgeDatabase(str(tmp_path / "bridge.db"))


def test_dual_publish_success(db: BridgeDatabase) -> None:
    calls: list[str] = []

    async def tg(sub: Submission) -> tuple[str, str]:
        calls.append("tg")
        return "-1001", "11"

    async def ds(sub: Submission) -> tuple[str, str]:
        calls.append("ds")
        return "chan", "22"

    router = PublishRouter(
        db=db, telegram_publish=tg, discord_publish=ds, retries=0
    )
    result = asyncio.run(router.publish(_submission(db)))
    assert calls == ["tg", "ds"]
    assert result.target_id == "-1001"
    assert result.message_id == "11"
    assert len(result.sides) == 2
    link = db.find_mirror_by_tg("-1001", "11")
    assert link is not None
    assert link.ds_message_id == "22"
    assert link.kind is MirrorKind.suggest_publish


def test_dual_publish_rollback_on_second_failure(db: BridgeDatabase) -> None:
    deleted: list[tuple[str, str]] = []

    async def tg(sub: Submission) -> tuple[str, str]:
        return "-1001", "11"

    async def ds(sub: Submission) -> tuple[str, str]:
        raise RuntimeError("ds down")

    async def tg_del(chat_id: str, message_id: str) -> None:
        deleted.append((chat_id, message_id))

    router = PublishRouter(
        db=db,
        telegram_publish=tg,
        discord_publish=ds,
        telegram_delete=tg_del,
        retries=0,
    )
    with pytest.raises(PublishRouterError):
        asyncio.run(router.publish(_submission(db)))
    assert deleted == [("-1001", "11")]
    assert db.find_mirror_by_tg("-1001", "11") is None


def test_publish_target_telegram_only(db: BridgeDatabase) -> None:
    calls: list[str] = []

    async def tg(sub: Submission) -> tuple[str, str]:
        calls.append("tg")
        return "-1001", "11"

    async def ds(sub: Submission) -> tuple[str, str]:
        calls.append("ds")
        return "chan", "22"

    router = PublishRouter(
        db=db, telegram_publish=tg, discord_publish=ds, retries=0
    )
    asyncio.run(
        router.publish(_submission(db, target=PublishTarget.telegram))
    )
    assert calls == ["tg"]


def test_mirror_enabled_setting(db: BridgeDatabase) -> None:
    assert is_mirror_enabled(db, default=True) is True
    set_mirror_enabled(db, False)
    assert is_mirror_enabled(db, default=True) is False
    set_mirror_enabled(db, True)
    assert is_mirror_enabled(db, default=True) is True


def test_mirror_link_dedup_lookup(db: BridgeDatabase) -> None:
    db.insert_mirror_link(
        MirrorLink(
            origin=Platform.telegram,
            kind=MirrorKind.channel_mirror,
            tg_chat_id="-100",
            tg_message_id="5",
            ds_channel_id="9",
            ds_message_id="8",
        )
    )
    assert db.find_mirror_by_tg("-100", "5") is not None
    assert db.find_mirror_by_ds("9", "8") is not None
    assert db.find_mirror_by_tg("-100", "6") is None


def test_publish_target_persisted(db: BridgeDatabase) -> None:
    stored = _submission(db, target=PublishTarget.discord)
    loaded = db.get_submission(int(stored.id or 0))
    assert loaded is not None
    assert loaded.publish_target is PublishTarget.discord
    db.update_submission(int(stored.id or 0), publish_target=PublishTarget.both)
    loaded = db.get_submission(int(stored.id or 0))
    assert loaded is not None
    assert loaded.publish_target is PublishTarget.both


def test_retry_then_success(db: BridgeDatabase) -> None:
    attempts = {"n": 0}

    async def tg(sub: Submission) -> tuple[str, str]:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("flaky")
        return "-1001", "11"

    router = PublishRouter(
        db=db,
        telegram_publish=tg,
        discord_publish=None,
        retries=2,
        retry_delay_sec=0.01,
    )
    result = asyncio.run(
        router.publish(_submission(db, target=PublishTarget.telegram))
    )
    assert result.message_id == "11"
    assert attempts["n"] == 2
