"""aiogram adapter for the suggest bridge: handlers, cards, publisher.

Wiring (see `bot/adapters/telegram/router.py`):

    db = BridgeDatabase(resolve_bridge_db_path())
    services = TelegramServices.from_database(
        db, bot=bot, channel_id=config.channel_id
    )
    services.admins.bootstrap_telegram_admins(config.admin_ids)
    dp.include_router(build_telegram_router(services))
"""

from bot.adapters.telegram.cards import TelegramCards, format_card
from bot.adapters.telegram.channel_publish import TelegramChannelPublisher
from bot.adapters.telegram.deps import (
    ServicesMiddleware,
    TelegramServices,
    is_telegram_admin,
)
from bot.adapters.telegram.event_sync import TelegramEventSync, notify_author
from bot.adapters.telegram.handlers_admin import build_admin_router
from bot.adapters.telegram.handlers_reply import build_reply_router
from bot.adapters.telegram.handlers_user import build_user_router
from bot.adapters.telegram.media import AlbumBuffer
from bot.adapters.telegram.publisher import PublishResult, TelegramPublisher
from bot.adapters.telegram.router import build_telegram_adapter, build_telegram_router

__all__ = [
    "AlbumBuffer",
    "PublishResult",
    "ServicesMiddleware",
    "TelegramCards",
    "TelegramChannelPublisher",
    "TelegramEventSync",
    "TelegramPublisher",
    "TelegramServices",
    "build_admin_router",
    "build_reply_router",
    "build_telegram_adapter",
    "build_telegram_router",
    "build_user_router",
    "format_card",
    "is_telegram_admin",
    "notify_author",
]
