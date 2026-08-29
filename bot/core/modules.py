"""Plugin API: external modules extend Suggest Bridge without editing core."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from aiogram import Bot, Dispatcher

from bot.config import BridgeConfig
from bot.core.db import BridgeDatabase
from bot.core.events import EventBus


@dataclass
class ModuleContext:
    """Runtime handles passed to each loaded module."""

    config: BridgeConfig
    db: BridgeDatabase
    bus: EventBus
    services: Any
    logger: logging.Logger
    telegram_bot: Bot | None = None
    dp: Dispatcher | None = None
    discord_bot: Any | None = None
    discord_ctx: Any | None = None


@runtime_checkable
class BridgeModule(Protocol):
    """Contract for SB_MODULES entries (`pkg.mod:ClassName`)."""

    name: str

    async def setup(self, ctx: ModuleContext) -> None: ...

    async def setup_telegram(self, ctx: ModuleContext) -> None: ...

    async def setup_discord(self, ctx: ModuleContext) -> None: ...

    async def teardown(self, ctx: ModuleContext) -> None: ...


class BaseBridgeModule:
    """Optional base class with empty hook defaults."""

    name: str = "unnamed"

    async def setup(self, ctx: ModuleContext) -> None:
        return None

    async def setup_telegram(self, ctx: ModuleContext) -> None:
        return None

    async def setup_discord(self, ctx: ModuleContext) -> None:
        return None

    async def teardown(self, ctx: ModuleContext) -> None:
        return None
