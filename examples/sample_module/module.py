"""Example third-party module — not loaded unless listed in SB_MODULES."""

from __future__ import annotations

import discord
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from discord import app_commands

from bot.core.modules import BaseBridgeModule, ModuleContext


class SampleModule(BaseBridgeModule):
    name = "sample"

    async def setup_telegram(self, ctx: ModuleContext) -> None:
        if ctx.dp is None:
            return
        router = Router(name="sample_module")

        @router.message(Command("sample_ping"))
        async def sample_ping(message: Message) -> None:
            await message.answer("sample module: pong (Telegram)")

        ctx.dp.include_router(router)

    async def setup_discord(self, ctx: ModuleContext) -> None:
        bot = ctx.discord_bot
        if bot is None:
            return

        @app_commands.command(
            name="sample_ping",
            description="Sample module ping (example for SB_MODULES)",
        )
        async def sample_ping(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                "sample module: pong (Discord)", ephemeral=True
            )

        bot.tree.add_command(sample_ping)
