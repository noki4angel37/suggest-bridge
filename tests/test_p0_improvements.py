"""P0: media cache, draft cancel, queue listing, moderator text edit."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from bot.adapters.admin_common import format_queue_report
from bot.core.db import BridgeDatabase
from bot.core.events import EventBus
from bot.core.media_store import (
    delete_submission_files,
    materialize_discord_media,
    materialize_from_blobs,
    media_root,
    store_bytes,
    submission_dir,
)
from bot.core.models import (
    ContentType,
    MediaItem,
    RefKind,
    Source,
    Submission,
    SubmissionStatus,
    SubmissionUpdated,
    utcnow,
)
from bot.core.services import ModerationService, SubmissionService


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


def test_media_root_next_to_db(tmp_path: Path) -> None:
    db_file = tmp_path / "data" / "bridge.db"
    db_file.parent.mkdir(parents=True)
    assert media_root(db_path=str(db_file)) == tmp_path / "data" / "media"


def test_materialize_discord_media_writes_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "bridge.db")
    url = "https://cdn.discordapp.com/attachments/1/2/photo.png"
    items = [
        MediaItem(
            content_type=ContentType.photo,
            order_index=0,
            discord_attachment_url=url,
        )
    ]

    async def fake_download(
        fetch_url: str,
        dest: Path,
        *,
        max_bytes: int = 0,
        timeout_sec: float = 0,
    ) -> Path | None:
        assert fetch_url == url
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-image")
        return dest

    monkeypatch.setattr("bot.core.media_store.download_url", fake_download)
    cached = asyncio.run(materialize_discord_media(7, items, db_path=db_path))
    assert len(cached) == 1
    assert cached[0].ref_kind is RefKind.local_path
    assert cached[0].local_path is not None
    assert Path(cached[0].local_path).is_file()
    assert Path(cached[0].local_path).read_bytes() == b"fake-image"


def test_materialize_from_blobs_replaces_cdn_ref(tmp_path: Path) -> None:
    db_path = str(tmp_path / "bridge.db")
    items = [
        MediaItem(
            content_type=ContentType.photo,
            order_index=0,
            discord_attachment_url="https://cdn.discordapp.com/attachments/1/2/a.png",
        )
    ]
    cached = materialize_from_blobs(
        8, items, {0: b"blob-bytes"}, db_path=db_path
    )
    assert len(cached) == 1
    assert cached[0].ref_kind is RefKind.local_path
    assert cached[0].discord_attachment_url is None
    assert cached[0].local_path is not None
    assert Path(cached[0].local_path).read_bytes() == b"blob-bytes"


def test_store_bytes_rejects_empty(tmp_path: Path) -> None:
    assert (
        store_bytes(
            1,
            b"",
            order_index=0,
            content_type=ContentType.photo,
            db_path=str(tmp_path / "bridge.db"),
        )
        is None
    )


def test_cache_discord_media_via_service(
    submissions: SubmissionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.discord,
            author_platform_user_id="1",
            author_display_name="A",
            text="с фото",
            media=[
                MediaItem(
                    content_type=ContentType.photo,
                    order_index=0,
                    discord_attachment_url="https://cdn.example/a.png",
                )
            ],
        )
    )
    assert draft.id is not None

    async def fake_download(
        url: str,
        dest: Path,
        *,
        max_bytes: int = 0,
        timeout_sec: float = 0,
    ) -> Path | None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"ok")
        return dest

    monkeypatch.setattr("bot.core.media_store.download_url", fake_download)
    updated = asyncio.run(submissions.cache_discord_media(draft.id))
    assert updated.media[0].ref_kind is RefKind.local_path
    assert Path(updated.media[0].local_path or "").is_file()


def test_cache_discord_blobs_via_service(submissions: SubmissionService) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.discord,
            author_platform_user_id="1",
            author_display_name="A",
            text="с фото",
            media=[
                MediaItem(
                    content_type=ContentType.photo,
                    order_index=0,
                    discord_attachment_url="https://cdn.example/a.png",
                )
            ],
        )
    )
    assert draft.id is not None
    updated = asyncio.run(
        submissions.cache_discord_blobs(draft.id, {0: b"from-api"})
    )
    assert updated.media[0].ref_kind is RefKind.local_path
    assert Path(updated.media[0].local_path or "").read_bytes() == b"from-api"


def test_cancel_draft_deletes_row_and_files(
    submissions: SubmissionService,
    db: BridgeDatabase,
) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.telegram,
            author_platform_user_id="9",
            author_display_name="B",
            text="черновик",
        )
    )
    assert draft.id is not None
    real_dir = submission_dir(draft.id, db_path=db.db_path)
    real_dir.mkdir(parents=True, exist_ok=True)
    (real_dir / "00_file.jpg").write_bytes(b"x")

    ok = asyncio.run(submissions.cancel_draft(draft.id))
    assert ok is True
    assert submissions.get(draft.id) is None
    assert not real_dir.exists()


def test_cancel_draft_rejects_pending(submissions: SubmissionService) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.telegram,
            author_platform_user_id="1",
            author_display_name="C",
            text="готово",
        )
    )
    assert draft.id is not None
    asyncio.run(submissions.submit(draft.id))
    with pytest.raises(ValueError):
        asyncio.run(submissions.cancel_draft(draft.id))


def test_edit_moderator_text_and_event(
    submissions: SubmissionService, bus: EventBus
) -> None:
    seen: list[SubmissionUpdated] = []

    async def on_updated(event: SubmissionUpdated) -> None:
        seen.append(event)

    bus.subscribe(SubmissionUpdated, on_updated)
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.telegram,
            author_platform_user_id="1",
            author_display_name="D",
            text="старый",
        )
    )
    assert draft.id is not None
    asyncio.run(submissions.submit(draft.id))
    updated = asyncio.run(
        submissions.edit_moderator_text(draft.id, "новый текст")
    )
    assert updated.text == "новый текст"
    assert any(e.submission.text == "новый текст" for e in seen)


def test_edit_moderator_text_rejects_published(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    draft = asyncio.run(
        submissions.create_draft(
            source=Source.telegram,
            author_platform_user_id="1",
            author_display_name="E",
            text="пост",
        )
    )
    assert draft.id is not None
    asyncio.run(submissions.submit(draft.id))
    asyncio.run(moderation.approve(draft.id))
    asyncio.run(moderation.mark_published(draft.id))
    with pytest.raises(ValueError):
        asyncio.run(submissions.edit_moderator_text(draft.id, "поздно"))


def test_list_scheduled_orders_by_time(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    now = utcnow()
    later_id = None
    sooner_id = None
    for offset, text in ((120, "позже"), (30, "раньше")):
        draft = asyncio.run(
            submissions.create_draft(
                source=Source.telegram,
                author_platform_user_id="1",
                author_display_name="F",
                text=text,
            )
        )
        assert draft.id is not None
        asyncio.run(submissions.submit(draft.id))
        asyncio.run(
            moderation.approve(
                draft.id, scheduled_at=now + timedelta(seconds=offset)
            )
        )
        if offset == 120:
            later_id = draft.id
        else:
            sooner_id = draft.id
    ordered = submissions.list_scheduled()
    assert [s.id for s in ordered] == [sooner_id, later_id]


def test_format_queue_marks_overdue() -> None:
    now = utcnow()
    pending = [
        Submission(
            source=Source.discord,
            author_platform_user_id="1",
            author_display_name="A",
            id=3,
            status=SubmissionStatus.pending,
            text="Идея из Discord",
        )
    ]
    scheduled = [
        Submission(
            source=Source.telegram,
            author_platform_user_id="2",
            author_display_name="B",
            id=5,
            status=SubmissionStatus.scheduled,
            text="Старый пост",
            scheduled_at=now - timedelta(minutes=5),
        )
    ]
    report = format_queue_report(pending, scheduled, now=now)
    assert "#3" in report and "[DS]" in report
    assert "#5" in report and "просрочено" in report


def test_delete_submission_files_missing_is_ok(tmp_path: Path) -> None:
    delete_submission_files(999, db_path=str(tmp_path / "bridge.db"))
