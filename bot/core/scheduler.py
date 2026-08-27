"""Background worker publishing submissions whose scheduled time has come."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from bot.core.db import BridgeDatabase
from bot.core.events import EventBus
from bot.core.models import (
    DomainEvent,
    Platform,
    Submission,
    SubmissionApproved,
    SubmissionScheduled,
    SubmissionStatus,
    utcnow,
)
from bot.core.publisher import extract_publish_ref
from bot.core.services import ModerationService

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SEC = 30.0
DEFAULT_BATCH_LIMIT = 20
MIN_SLEEP_SEC = 0.5

PublishCallback = Callable[[Submission], Awaitable[object]]


class Scheduler:
    """Polls the bridge DB for due submissions and publishes them.

    Domain events only wake the loop early; the DB stays the source of truth, so
    a restart picks up whatever was scheduled while the bot was down.
    """

    def __init__(
        self,
        db: BridgeDatabase,
        moderation: ModerationService,
        publish_callback: PublishCallback,
        *,
        bus: EventBus | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        platform: Platform = Platform.telegram,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db = db
        self.moderation = moderation
        self.publish_callback = publish_callback
        self.bus = bus
        self.poll_interval = poll_interval
        self.batch_limit = batch_limit
        self.platform = platform
        self._now = now
        self._task: asyncio.Task[None] | None = None
        self._wakeup = asyncio.Event()
        self._in_flight: set[int] = set()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._subscribe()
        self._wakeup.clear()
        self._task = asyncio.create_task(self._run(), name="publish-scheduler")
        logger.info(
            "Планировщик публикаций запущен, опрос каждые %.0f с",
            self.poll_interval,
        )

    async def stop(self) -> None:
        self._unsubscribe()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Планировщик публикаций остановлен")

    def wake(self) -> None:
        """Ask the loop to re-check the queue without waiting for the timer."""
        self._wakeup.set()

    def due_submissions(self) -> list[Submission]:
        now = self._now()
        due: list[Submission] = []
        for submission in self.db.list_submissions(
            status=SubmissionStatus.scheduled, limit=self.batch_limit
        ):
            if submission.id is None or submission.id in self._in_flight:
                continue
            if submission.scheduled_at is None or submission.scheduled_at <= now:
                due.append(submission)
        return due

    async def tick(self) -> list[int]:
        """Publish everything that is due; returns published submission ids."""
        published: list[int] = []
        for submission in self.due_submissions():
            submission_id = submission.id
            if submission_id is None:
                continue
            if await self._publish_one(submission):
                published.append(submission_id)
        return published

    async def _publish_one(self, submission: Submission) -> bool:
        submission_id = submission.id
        assert submission_id is not None
        self._in_flight.add(submission_id)
        try:
            try:
                result = await self.publish_callback(submission)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Не удалось опубликовать заявку %s, повтор позже",
                    submission_id,
                )
                return False

            try:
                target_id, message_id = extract_publish_ref(result)
                await self.moderation.mark_published(
                    submission_id,
                    platform=self.platform,
                    target_id=target_id,
                    message_id=message_id,
                )
            except Exception:
                logger.exception(
                    "mark_published failed after publish for %s", submission_id
                )
                if not self.db.update_submission_cas(
                    submission_id,
                    (
                        SubmissionStatus.approved,
                        SubmissionStatus.scheduled,
                    ),
                    status=SubmissionStatus.published,
                    published_at=utcnow(),
                ):
                    return False

            logger.info(
                "Заявка %s опубликована по расписанию", submission_id
            )
            return True
        finally:
            self._in_flight.discard(submission_id)

    async def _run(self) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Сбой цикла планировщика публикаций")
                await self._sleep()
        except asyncio.CancelledError:
            return

    async def _sleep(self) -> None:
        self._wakeup.clear()
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=self._next_delay())
        except (asyncio.TimeoutError, TimeoutError):
            pass

    def _next_delay(self) -> float:
        """Sleep until the nearest scheduled post, capped by the poll interval."""
        now = self._now()
        nearest: float | None = None
        for submission in self.db.list_submissions(
            status=SubmissionStatus.scheduled, limit=self.batch_limit
        ):
            if submission.scheduled_at is None or submission.scheduled_at <= now:
                continue
            delta = (submission.scheduled_at - now).total_seconds()
            nearest = delta if nearest is None else min(nearest, delta)
        if nearest is None:
            return self.poll_interval
        return max(MIN_SLEEP_SEC, min(self.poll_interval, nearest))

    def _subscribe(self) -> None:
        if self.bus is None:
            return
        self.bus.subscribe(SubmissionScheduled, self._on_event)
        self.bus.subscribe(SubmissionApproved, self._on_event)

    def _unsubscribe(self) -> None:
        if self.bus is None:
            return
        self.bus.unsubscribe(SubmissionScheduled, self._on_event)
        self.bus.unsubscribe(SubmissionApproved, self._on_event)

    async def _on_event(self, event: DomainEvent) -> None:
        self.wake()
