from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bot.core.approve_flow import finalize_approval
from bot.core.db import BridgeDatabase
from bot.core.events import EventBus
from bot.core.models import (
    Platform,
    Source,
    Submission,
    SubmissionStatus,
    utcnow,
)
from bot.core.scheduler import Scheduler
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


class RecordingPublisher:
    """Publish callback stub returning a Telegram-like result object."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls: list[int] = []
        self.fail_times = fail_times

    async def __call__(self, submission: Submission) -> object:
        assert submission.id is not None
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("Канал недоступен")
        self.calls.append(submission.id)
        return type(
            "Result",
            (),
            {"target_id": "-100500", "message_id": 1000 + submission.id},
        )()


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


def test_due_submissions_ignores_future_posts(
    db: BridgeDatabase,
    submissions: SubmissionService,
    moderation: ModerationService,
) -> None:
    now = utcnow()
    due_id = make_pending(submissions)
    later_id = make_pending(submissions)
    asyncio.run(moderation.schedule(due_id, now - timedelta(minutes=1)))
    asyncio.run(moderation.schedule(later_id, now + timedelta(hours=1)))

    scheduler = Scheduler(db, moderation, RecordingPublisher(), now=lambda: now)
    assert [s.id for s in scheduler.due_submissions()] == [due_id]


def test_tick_publishes_due_and_marks_published(
    db: BridgeDatabase,
    submissions: SubmissionService,
    moderation: ModerationService,
) -> None:
    now = utcnow()
    submission_id = make_pending(submissions)
    asyncio.run(moderation.schedule(submission_id, now - timedelta(seconds=1)))

    publisher = RecordingPublisher()
    scheduler = Scheduler(db, moderation, publisher, now=lambda: now)
    assert asyncio.run(scheduler.tick()) == [submission_id]
    assert publisher.calls == [submission_id]

    stored = db.get_submission(submission_id)
    assert stored is not None
    assert stored.status is SubmissionStatus.published
    assert stored.published_at is not None


def test_tick_is_idempotent_after_publishing(
    db: BridgeDatabase,
    submissions: SubmissionService,
    moderation: ModerationService,
) -> None:
    now = utcnow()
    submission_id = make_pending(submissions)
    asyncio.run(moderation.schedule(submission_id, now))

    publisher = RecordingPublisher()
    scheduler = Scheduler(db, moderation, publisher, now=lambda: now)
    asyncio.run(scheduler.tick())
    assert asyncio.run(scheduler.tick()) == []
    assert publisher.calls == [submission_id]


def test_failed_publish_keeps_submission_scheduled(
    db: BridgeDatabase,
    submissions: SubmissionService,
    moderation: ModerationService,
) -> None:
    now = utcnow()
    submission_id = make_pending(submissions)
    asyncio.run(moderation.schedule(submission_id, now))

    publisher = RecordingPublisher(fail_times=1)
    scheduler = Scheduler(db, moderation, publisher, now=lambda: now)
    assert asyncio.run(scheduler.tick()) == []
    stored = db.get_submission(submission_id)
    assert stored is not None
    assert stored.status is SubmissionStatus.scheduled

    assert asyncio.run(scheduler.tick()) == [submission_id]


def test_start_publishes_due_post_and_stops_cleanly(
    db: BridgeDatabase,
    submissions: SubmissionService,
    moderation: ModerationService,
    bus: EventBus,
) -> None:
    submission_id = make_pending(submissions)
    publisher = RecordingPublisher()

    async def scenario() -> None:
        scheduler = Scheduler(
            db, moderation, publisher, bus=bus, poll_interval=60
        )
        await scheduler.start()
        assert scheduler.is_running
        # The event wakes the loop instead of waiting for the poll interval.
        await moderation.schedule(submission_id, utcnow() - timedelta(seconds=1))
        for _ in range(100):
            if publisher.calls:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop()
        assert not scheduler.is_running

    asyncio.run(scenario())
    assert publisher.calls == [submission_id]


def test_finalize_approval_publishes_immediately(
    db: BridgeDatabase,
    submissions: SubmissionService,
    moderation: ModerationService,
) -> None:
    submission_id = make_pending(submissions)
    publisher = RecordingPublisher()

    outcome = asyncio.run(
        finalize_approval(
            moderation,
            submission_id=submission_id,
            with_author=True,
            publish_at=None,
            publish_now_cb=publisher,
            submissions=submissions,
            moderator_platform=Platform.telegram,
            moderator_id="1",
        )
    )

    assert outcome.published is True
    assert outcome.scheduled is False
    assert outcome.message_id == str(1000 + submission_id)
    stored = db.get_submission(submission_id)
    assert stored is not None
    assert stored.status is SubmissionStatus.published
    assert stored.want_anonymous is False


def test_finalize_approval_schedules_without_publishing(
    db: BridgeDatabase,
    submissions: SubmissionService,
    moderation: ModerationService,
) -> None:
    submission_id = make_pending(submissions)
    publisher = RecordingPublisher()
    publish_at = utcnow() + timedelta(hours=2)

    outcome = asyncio.run(
        finalize_approval(
            moderation,
            submission_id=submission_id,
            with_author=False,
            publish_at=publish_at,
            publish_now_cb=publisher,
        )
    )

    assert outcome.scheduled is True
    assert outcome.published is False
    assert publisher.calls == []
    stored = db.get_submission(submission_id)
    assert stored is not None
    assert stored.status is SubmissionStatus.scheduled
    assert stored.want_anonymous is True
    assert isinstance(stored.scheduled_at, datetime)


def test_finalize_approval_is_idempotent(
    submissions: SubmissionService, moderation: ModerationService
) -> None:
    submission_id = make_pending(submissions)
    publisher = RecordingPublisher()

    async def scenario() -> tuple[bool, bool]:
        first = await finalize_approval(
            moderation,
            submission_id=submission_id,
            with_author=True,
            publish_at=None,
            publish_now_cb=publisher,
        )
        second = await finalize_approval(
            moderation,
            submission_id=submission_id,
            with_author=True,
            publish_at=None,
            publish_now_cb=publisher,
        )
        return first.published, second.already_handled

    published, already_handled = asyncio.run(scenario())
    assert published is True
    assert already_handled is True
    assert publisher.calls == [submission_id]


def test_scheduled_post_flows_through_scheduler(
    db: BridgeDatabase,
    submissions: SubmissionService,
    moderation: ModerationService,
) -> None:
    """Moderator schedules, scheduler publishes once the time has passed."""
    submission_id = make_pending(submissions)
    publisher = RecordingPublisher()
    publish_at = utcnow() + timedelta(minutes=30)

    asyncio.run(
        finalize_approval(
            moderation,
            submission_id=submission_id,
            with_author=True,
            publish_at=publish_at,
            publish_now_cb=publisher,
        )
    )
    scheduler = Scheduler(
        db, moderation, publisher, now=lambda: publish_at + timedelta(seconds=1)
    )
    assert asyncio.run(scheduler.tick()) == [submission_id]

    stored = db.get_submission(submission_id)
    assert stored is not None
    assert stored.status is SubmissionStatus.published
