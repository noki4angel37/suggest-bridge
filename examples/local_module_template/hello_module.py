"""Minimal local module template — copy outside this repo, then SB_MODULES."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.core.modules import BaseBridgeModule, ModuleContext


class HelloModule(BaseBridgeModule):
    """Replace `name` and handlers; keep the file on your machine."""

    name = "hello"

    async def setup(self, ctx: ModuleContext) -> None:
        ctx.logger.info("hello module loaded")

    async def setup_telegram(self, ctx: ModuleContext) -> None:
        if ctx.dp is None:
            return
        router = Router(name="hello_module")

        @router.message(Command("hello"))
        async def hello_cmd(message: Message) -> None:
            await message.answer("Hello from your local SB_MODULES module")

        ctx.dp.include_router(router)
