"""Discord adapter (discord.py 2.x) for the suggest bridge.

Русский интерфейс, мультисервер. Точка входа — `start_discord(token, services)`.
Требуемые intents: `message_content`, `members`, `guilds`.

Note: `import discord` inside this package resolves to the discord.py library,
Python 3 imports are absolute.
"""

from __future__ import annotations

from bot.adapters.discord.bot_app import (
    SuggestBot,
    create_bot,
    default_intents,
    start_discord,
)
from bot.adapters.discord.context import (
    BridgeServices,
    DiscordContext,
    NotifyHook,
    PublishHook,
    ServiceBundle,
    resolve_services,
)

__all__ = [
    "BridgeServices",
    "DiscordContext",
    "NotifyHook",
    "PublishHook",
    "ServiceBundle",
    "resolve_services",
    "SuggestBot",
    "create_bot",
    "default_intents",
    "start_discord",
]
