"""Append-only JSONL operator event log (for ppctl Events pane).

Never write tokens. Rotation: rename to `.1` when file exceeds MAX_BYTES.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.core.models import (
    AdminChanged,
    DomainEvent,
    SubmissionApproved,
    SubmissionPublished,
    SubmissionRejected,
    SubmissionScheduled,
    SubmissionSubmitted,
    UserBlocked,
    UserUnblocked,
)

logger = logging.getLogger(__name__)

SVC_ID = "suggest-bridge"
MAX_BYTES = 2 * 1024 * 1024
_lock = threading.Lock()
_path: Path | None = None


def resolve_event_log_path() -> Path:
    raw = (os.environ.get("SUGGEST_EVENT_LOG") or "").strip()
    if raw:
        return Path(raw)
    # Default next to bridge DB / data/
    here = Path(__file__).resolve().parents[2]  # apps/telegram-suggest-bot
    return here / "data" / "events.jsonl"


def configure_event_log(path: Path | str | None = None) -> Path:
    global _path
    _path = Path(path) if path else resolve_event_log_path()
    _path.parent.mkdir(parents=True, exist_ok=True)
    return _path


def _ensure_path() -> Path:
    global _path
    if _path is None:
        return configure_event_log()
    return _path


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.is_file() and path.stat().st_size >= MAX_BYTES:
            backup = path.with_suffix(path.suffix + ".1")
            if backup.exists():
                backup.unlink()
            path.rename(backup)
    except OSError:
        logger.warning("event_log rotate failed for %s", path, exc_info=True)


def append_event(
    event_type: str,
    *,
    summary: str,
    actor: str | None = None,
    data: dict[str, Any] | None = None,
    svc: str = SVC_ID,
) -> None:
    """Best-effort append one JSON line. Never raises to callers."""
    try:
        path = _ensure_path()
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "svc": svc,
            "type": event_type,
            "actor": actor or "",
            "summary": summary[:500],
            "data": data or {},
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with _lock:
            _rotate_if_needed(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:  # noqa: BLE001
        logger.warning("event_log append failed", exc_info=True)


def _domain_to_record(event: DomainEvent) -> tuple[str, str, str | None, dict[str, Any]] | None:
    if isinstance(event, SubmissionSubmitted):
        sub = event.submission
        return (
            "submission.submitted",
            f"Заявка #{sub.id} отправлена ({sub.source.value})",
            f"{sub.source.value}:{sub.author_platform_user_id}",
            {"submission_id": sub.id, "source": sub.source.value},
        )
    if isinstance(event, SubmissionApproved):
        sub = event.submission
        actor = None
        if event.moderator_platform and event.moderator_id:
            actor = f"{event.moderator_platform.value}:{event.moderator_id}"
        return (
            "moderation.approved",
            f"Заявка #{sub.id} одобрена",
            actor,
            {"submission_id": sub.id},
        )
    if isinstance(event, SubmissionRejected):
        sub = event.submission
        actor = None
        if event.moderator_platform and event.moderator_id:
            actor = f"{event.moderator_platform.value}:{event.moderator_id}"
        return (
            "moderation.rejected",
            f"Заявка #{sub.id} отклонена",
            actor,
            {"submission_id": sub.id},
        )
    if isinstance(event, SubmissionScheduled):
        sub = event.submission
        when = event.scheduled_at or getattr(sub, "scheduled_at", None)
        return (
            "submission.scheduled",
            f"Заявка #{sub.id} отложена → {when or '?'}",
            None,
            {
                "submission_id": sub.id,
                "scheduled_at": when.isoformat() if when else None,
            },
        )
    if isinstance(event, SubmissionPublished):
        sub = event.submission
        plat = event.platform.value if event.platform else "?"
        return (
            "post.published",
            f"Заявка #{sub.id} опубликована → {plat}",
            None,
            {
                "submission_id": sub.id,
                "platform": plat,
                "target_id": event.target_id,
                "message_id": event.message_id,
            },
        )
    if isinstance(event, AdminChanged):
        return (
            "admin.changed",
            f"Админ {event.admin.platform.value}:{event.admin.platform_user_id} {event.action}",
            None,
            {
                "action": event.action,
                "platform": event.admin.platform.value,
                "user_id": event.admin.platform_user_id,
            },
        )
    if isinstance(event, UserBlocked):
        e = event.entry
        return (
            "user.blocked",
            f"Блок {e.platform.value}:{e.platform_user_id}",
            None,
            {"platform": e.platform.value, "user_id": e.platform_user_id},
        )
    if isinstance(event, UserUnblocked):
        return (
            "user.unblocked",
            f"Разблок {event.platform.value}:{event.platform_user_id}",
            None,
            {"platform": event.platform.value, "user_id": event.platform_user_id},
        )
    return None


async def on_domain_event(event: DomainEvent) -> None:
    mapped = _domain_to_record(event)
    if mapped is None:
        return
    event_type, summary, actor, data = mapped
    append_event(event_type, summary=summary, actor=actor, data=data)


def attach_event_log(bus: Any) -> None:
    """Subscribe JSONL sink to DomainEvent (and all subclasses via isinstance)."""
    from bot.core.models import DomainEvent as DE

    if _path is None:
        configure_event_log()
    bus.subscribe(DE, on_domain_event)
