"""In-process asyncio event bus for adapters (Telegram / Discord)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from bot.core.models import DomainEvent

logger = logging.getLogger(__name__)

EventT = TypeVar("EventT", bound=DomainEvent)
Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    """Fan-out of domain events to async handlers.

    Handlers subscribed to a base event class also receive subclass events.
    A failing handler is logged and never breaks the bus or other handlers.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[Handler]] = {}

    def subscribe(
        self, event_type: type[EventT], handler: Callable[[EventT], Awaitable[None]]
    ) -> None:
        self._handlers.setdefault(event_type, []).append(handler)  # type: ignore[arg-type]

    def unsubscribe(
        self, event_type: type[EventT], handler: Callable[[EventT], Awaitable[None]]
    ) -> None:
        handlers = self._handlers.get(event_type)
        if not handlers:
            return
        try:
            handlers.remove(handler)  # type: ignore[arg-type]
        except ValueError:
            pass

    def handlers_for(self, event: DomainEvent) -> list[Handler]:
        matched: list[Handler] = []
        for event_type, handlers in self._handlers.items():
            if isinstance(event, event_type):
                matched.extend(handlers)
        return matched

    async def publish(self, event: DomainEvent) -> None:
        handlers = self.handlers_for(event)
        if not handlers:
            return
        results = await asyncio.gather(
            *(handler(event) for handler in handlers), return_exceptions=True
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, BaseException):
                logger.exception(
                    "Event handler %r failed on %s",
                    getattr(handler, "__qualname__", handler),
                    type(event).__name__,
                    exc_info=result,
                )
