"""Operator-visible bot actions for the ppctl Events pane."""

from __future__ import annotations

from typing import Any

from bot.core.event_log import append_event


def log_bot_action(
    summary: str,
    *,
    action: str,
    actor: str | None = None,
    **data: Any,
) -> None:
    """Record what the bot *did* (delete, reply, grant, publish, …)."""
    payload = {"action": action, **data}
    append_event(
        "bot.action",
        summary=summary[:500],
        actor=actor,
        data=payload,
    )
