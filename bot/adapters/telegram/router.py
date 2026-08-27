"""Assembly point of the Telegram adapter."""

from __future__ import annotations

from aiogram import Bot, Router

from bot.adapters.telegram.command_log import CommandEventLogMiddleware
from bot.adapters.telegram.deps import ServicesMiddleware, TelegramServices
from bot.adapters.telegram.handlers_admin import build_admin_router
from bot.adapters.telegram.handlers_host import register_telegram_host
from bot.adapters.telegram.handlers_reply import build_reply_router
from bot.adapters.telegram.handlers_settings import register_telegram_settings
from bot.adapters.telegram.handlers_user import build_user_router
from bot.adapters.telegram.media import ALBUM_FLUSH_DELAY
from bot.core import (
    AdminService,
    AntifloodService,
    BlacklistService,
    EventBus,
    GuildConfigService,
    ModerationService,
    SubmissionService,
)


def build_telegram_router(
    services: TelegramServices,
    *,
    attach_events: bool = True,
    include_settings: bool = True,
) -> Router:
    """Router with services injected, admin routers first, user router last.

    Order matters: admin callbacks, admin text states and admin commands must
    win over the catch-all private-chat handlers of the author flow. Every call
    builds fresh routers, so one process can host more than one bot.
    """
    root = Router(name="telegram-adapter")
    middleware = ServicesMiddleware(services)
    # Outer middleware on the root observers also feeds nested routers and filters.
    root.message.outer_middleware(middleware)
    root.message.outer_middleware(CommandEventLogMiddleware())
    root.callback_query.outer_middleware(middleware)

    root.include_router(build_admin_router())
    root.include_router(build_reply_router())
    if include_settings:
        register_telegram_settings(root, services)
        register_telegram_host(root, services)
    root.include_router(build_user_router())

    if attach_events:
        services.events.attach(services.bus)
    return root


def build_telegram_adapter(
    *,
    bot: Bot,
    bus: EventBus,
    submissions: SubmissionService,
    moderation: ModerationService,
    admins: AdminService,
    blacklist: BlacklistService,
    antiflood: AntifloodService,
    channel_id: int | str,
    guilds: GuildConfigService | None = None,
    album_delay: float = ALBUM_FLUSH_DELAY,
    attach_events: bool = True,
    include_settings: bool = True,
) -> tuple[Router, TelegramServices]:
    """Convenience wiring when core services are already built elsewhere."""
    services = TelegramServices(
        bot=bot,
        bus=bus,
        submissions=submissions,
        moderation=moderation,
        admins=admins,
        blacklist=blacklist,
        antiflood=antiflood,
        channel_id=channel_id,
        album_delay=album_delay,
        guilds=guilds,
    )
    router = build_telegram_router(
        services,
        attach_events=attach_events,
        include_settings=include_settings,
    )
    return router, services
