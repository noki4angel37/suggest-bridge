"""Example third-party module — not loaded unless listed in SB_MODULES."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import discord
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from discord import app_commands

from bot.core.models import DomainEvent
from bot.core.modules import BaseBridgeModule, ModuleContext


class SampleModule(BaseBridgeModule):
    name = "sample"

    def __init__(self) -> None:
        self._bus_handler: Callable[[Any], Awaitable[None]] | None = None

    async def setup(self, ctx: ModuleContext) -> None:
        async def _on_event(event: DomainEvent) -> None:
            ctx.logger.debug("sample module saw %s", type(event).__name__)

        self._bus_handler = _on_event
        ctx.bus.subscribe(DomainEvent, _on_event)
        ctx.logger.info("sample module subscribed to DomainEvent")

    async def setup_telegram(self, ctx: ModuleContext) -> None:
        if ctx.dp is None:
            return
        router = Router(name="sample_module")

        @router.message(Command("sample_ping"))
        async def sample_ping(message: Message) -> None:
            await message.answer("модуль sample: pong (Telegram)")

        ctx.dp.include_router(router)

    async def setup_discord(self, ctx: ModuleContext) -> None:
        bot = ctx.discord_bot
        if bot is None:
            return

        @app_commands.command(
            name="sample_ping",
            description="Пинг примера модуля (SB_MODULES)",
        )
        async def sample_ping(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                "модуль sample: pong (Discord)", ephemeral=True
            )

        bot.tree.add_command(sample_ping)

    async def teardown(self, ctx: ModuleContext) -> None:
        if self._bus_handler is not None:
            ctx.bus.unsubscribe(DomainEvent, self._bus_handler)
            self._bus_handler = None
        ctx.logger.info(
            "sample module teardown (discord_bot=%s)",
            ctx.discord_bot is not None,
        )
