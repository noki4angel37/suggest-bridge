"""Dependency container, middleware and filters for the Telegram adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject

from bot.adapters.telegram.cards import TelegramCards
from bot.adapters.telegram.event_sync import TelegramEventSync
from bot.adapters.telegram.media import ALBUM_FLUSH_DELAY, AlbumBuffer
from bot.adapters.telegram.publisher import TelegramPublisher
from bot.core import (
    AdminService,
    AntifloodService,
    BlacklistService,
    BridgeDatabase,
    EventBus,
    GuildConfigService,
    ModerationService,
    Platform,
    Submission,
    SubmissionService,
)
from bot.core.publish_router import PublishRouter


@dataclass
class TelegramServices:
    """Everything the Telegram handlers need, assembled once at startup."""

    bot: Bot
    bus: EventBus
    submissions: SubmissionService
    moderation: ModerationService
    admins: AdminService
    blacklist: BlacklistService
    antiflood: AntifloodService
    channel_id: int | str
    album_delay: float = ALBUM_FLUSH_DELAY
    # Guild config is Discord-only, but the shared admin commands ask for it.
    guilds: GuildConfigService | None = None
    publish_router: PublishRouter | None = None
    mirror: Any = None
    publisher: TelegramPublisher = field(init=False)
    cards: TelegramCards = field(init=False)
    events: TelegramEventSync = field(init=False)
    albums: AlbumBuffer = field(init=False)

    def __post_init__(self) -> None:
        if self.guilds is None:
            self.guilds = GuildConfigService(self.submissions.db)
        self.publisher = TelegramPublisher(
            self.bot, self.channel_id, moderation=self.moderation
        )
        self.cards = TelegramCards(
            self.bot, moderation=self.moderation, admins=self.admins
        )
        self.events = TelegramEventSync(self.bot, cards=self.cards)
        self.albums = AlbumBuffer(delay=self.album_delay)

    async def publish_submission(
        self, submission: Submission, *, with_author: bool | None = None
    ) -> object:
        """Prefer dual-publish router when wired; otherwise Telegram-only."""
        if self.publish_router is not None:
            return await self.publish_router.publish(submission)
        return await self.publisher.publish(submission, with_author=with_author)

    @classmethod
    def from_database(
        cls,
        db: BridgeDatabase,
        *,
        bot: Bot,
        channel_id: int | str,
        bus: EventBus | None = None,
        album_delay: float = ALBUM_FLUSH_DELAY,
    ) -> TelegramServices:
        """Build the core services on one bridge DB and one shared event bus."""
        shared_bus = bus if bus is not None else EventBus()
        return cls(
            bot=bot,
            bus=shared_bus,
            submissions=SubmissionService(db, shared_bus),
            moderation=ModerationService(db, shared_bus),
            admins=AdminService(db, shared_bus),
            blacklist=BlacklistService(db, shared_bus),
            antiflood=AntifloodService(db),
            channel_id=channel_id,
            album_delay=album_delay,
            guilds=GuildConfigService(db),
        )

    def is_admin(self, user_id: int | str) -> bool:
        return self.admins.can_manage(Platform.telegram, str(user_id))

    def is_blocked(self, user_id: int | str) -> bool:
        return self.blacklist.is_blocked(Platform.telegram, str(user_id))


class ServicesMiddleware(BaseMiddleware):
    """Injects `services` into handlers and filters of the adapter routers."""

    def __init__(self, services: TelegramServices) -> None:
        self.services = services

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["services"] = self.services
        return await handler(event, data)


async def is_telegram_admin(
    event: TelegramObject, services: TelegramServices | None = None
) -> bool:
    """Admins come from AdminService, so /admin changes apply without restart."""
    user = getattr(event, "from_user", None)
    if user is None or services is None:
        return False
    return services.is_admin(user.id)
