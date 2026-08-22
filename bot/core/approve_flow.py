"""Shared approve → publish/schedule flow used by both platform adapters."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from bot.core import rules
from bot.core.models import Platform, Submission
from bot.core.publisher import extract_publish_ref
from bot.core.services import (
    ModerationService,
    SubmissionNotFoundError,
    SubmissionService,
)

logger = logging.getLogger(__name__)

PublishNowCallback = Callable[[Submission], Awaitable[object]]


@dataclass(frozen=True)
class ApprovalOutcome:
    submission: Submission
    scheduled: bool = False
    published: bool = False
    already_handled: bool = False
    scheduled_at: datetime | None = None
    target_id: str | None = None
    message_id: str | None = None


async def finalize_approval(
    moderation: ModerationService,
    *,
    submission_id: int,
    with_author: bool,
    publish_at: datetime | None,
    publish_now_cb: PublishNowCallback | None = None,
    submissions: SubmissionService | None = None,
    moderator_platform: Platform | None = None,
    moderator_id: str | None = None,
    platform: Platform = Platform.telegram,
) -> ApprovalOutcome:
    """Approve a submission and either schedule it or publish it right away.

    The moderator's "with author / anonymously" choice is persisted as
    `want_anonymous`, so the publish plan built later (by the scheduler or by
    `publish_now_cb`) shows the same author line.

    A failing `publish_now_cb` propagates and leaves the submission approved but
    unpublished, so a moderator can retry.
    """
    current = moderation.db.get_submission(submission_id)
    if current is None:
        raise SubmissionNotFoundError(f"Заявка {submission_id} не найдена")
    if rules.is_handled(current.status):
        return ApprovalOutcome(
            submission=current,
            already_handled=True,
            scheduled_at=current.scheduled_at,
        )

    await _persist_privacy(
        moderation,
        submissions,
        submission_id=submission_id,
        with_author=with_author,
    )

    result = await moderation.approve(
        submission_id,
        moderator_platform=moderator_platform,
        moderator_id=moderator_id,
        scheduled_at=publish_at,
    )
    if result.already_handled:
        return ApprovalOutcome(
            submission=result.submission,
            already_handled=True,
            scheduled_at=result.submission.scheduled_at,
        )

    if publish_at is not None:
        logger.info(
            "Заявка %s запланирована на %s", submission_id, publish_at.isoformat()
        )
        return ApprovalOutcome(
            submission=result.submission,
            scheduled=True,
            scheduled_at=publish_at,
        )

    if publish_now_cb is None:
        raise ValueError(
            "Нужен publish_now_cb: публикация без отложенного времени"
        )

    published_ref = await publish_now_cb(result.submission)
    target_id, message_id = extract_publish_ref(published_ref)
    marked = await moderation.mark_published(
        submission_id,
        platform=platform,
        target_id=target_id,
        message_id=message_id,
    )
    logger.info("Заявка %s опубликована", submission_id)
    return ApprovalOutcome(
        submission=marked.submission,
        published=True,
        target_id=target_id,
        message_id=message_id,
    )


async def _persist_privacy(
    moderation: ModerationService,
    submissions: SubmissionService | None,
    *,
    submission_id: int,
    with_author: bool,
) -> None:
    if submissions is not None:
        await submissions.set_privacy(
            submission_id, want_anonymous=not with_author
        )
        return
    moderation.db.update_submission(
        submission_id, want_anonymous=not with_author
    )
