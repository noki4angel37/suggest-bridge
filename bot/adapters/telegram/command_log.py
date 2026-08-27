"""Middleware: log Telegram slash-like /commands to the operator event log."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class CommandEventLogMiddleware(BaseMiddleware):
    """Log private/group messages that start with /command (best-effort)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text:
            text = event.text.strip()
            if text.startswith("/"):
                cmd = text.split()[0].split("@", 1)[0]
                user = event.from_user
                actor = f"tg:{user.id}" if user else None
                try:
                    from bot.core.event_log import append_event

                    append_event(
                        "command.telegram",
                        summary=f"{cmd} от {actor or '?'}",
                        actor=actor,
                        data={
                            "command": cmd,
                            "chat_id": event.chat.id if event.chat else None,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
        return await handler(event, data)
