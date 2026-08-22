"""Runtime context for the Discord adapter: core services plus hooks.

Nothing here imports discord.py, so the container can be built by any entry
point (Agent E) and unit-tested without a Discord connection.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from bot.core.db import BridgeDatabase
from bot.core.events import EventBus
from bot.core.models import GuildConfig, Platform, Source, Submission
from bot.core.services import (
    AdminService,
    AntifloodService,
    BlacklistService,
    GuildConfigService,
    ModerationService,
    SubmissionService,
)

# Optional injection points. Publishing to the Telegram channel belongs to the
# Telegram adapter / scheduler; the Discord adapter only calls these if given.
PublishHook = Callable[[Submission], Awaitable[None]]
# Returns True when the author was actually reached on the other platform.
NotifyHook = Callable[[Submission, str], Awaitable[bool]]


class BridgeServices(Protocol):
    """Attribute set the Discord adapter needs from the service container."""

    bus: EventBus
    submissions: SubmissionService
    moderation: ModerationService
    admins: AdminService
    guilds: GuildConfigService
    blacklist: BlacklistService
    antiflood: AntifloodService


@dataclass
class ServiceBundle:
    """Ready-made `BridgeServices` for standalone runs and tests."""

    bus: EventBus
    submissions: SubmissionService
    moderation: ModerationService
    admins: AdminService
    guilds: GuildConfigService
    blacklist: BlacklistService
    antiflood: AntifloodService

    @classmethod
    def from_db(
        cls, db: BridgeDatabase, *, bus: EventBus | None = None
    ) -> ServiceBundle:
        event_bus = bus or EventBus()
        return cls(
            bus=event_bus,
            submissions=SubmissionService(db, event_bus),
            moderation=ModerationService(db, event_bus),
            admins=AdminService(db, event_bus),
            guilds=GuildConfigService(db),
            blacklist=BlacklistService(db, event_bus),
            antiflood=AntifloodService(db),
        )


REQUIRED_SERVICE_ATTRS = (
    "bus",
    "submissions",
    "moderation",
    "admins",
    "blacklist",
    "antiflood",
    "guilds",
)


def resolve_services(services: Any) -> BridgeServices:
    """Accept any service container, e.g. the Telegram adapter's one.

    Containers built for Telegram carry no `guilds` service (guild configs are
    Discord-only), so it is created from the same bridge DB when missing.
    """
    missing = [
        name
        for name in REQUIRED_SERVICE_ATTRS
        if getattr(services, name, None) is None
    ]
    if not missing:
        return services
    db = _find_database(services)
    if missing == ["guilds"] and db is not None:
        return ServiceBundle(
            bus=services.bus,
            submissions=services.submissions,
            moderation=services.moderation,
            admins=services.admins,
            guilds=GuildConfigService(db),
            blacklist=services.blacklist,
            antiflood=services.antiflood,
        )
    raise TypeError(
        "Discord-адаптеру не хватает сервисов: " + ", ".join(sorted(missing))
    )


def _find_database(services: Any) -> BridgeDatabase | None:
    for holder in (
        services,
        getattr(services, "moderation", None),
        getattr(services, "submissions", None),
    ):
        db = getattr(holder, "db", None)
        if isinstance(db, BridgeDatabase):
            return db
    return None


@dataclass
class DiscordContext:
    """Services plus adapter-level configuration and optional hooks."""

    services: BridgeServices
    publish: PublishHook | None = None
    notify_telegram_author: NotifyHook | None = None
    # Which submission sources get a moderation card in Discord (TG + DS).
    mirror_sources: tuple[Source, ...] = (Source.telegram, Source.discord)
    # Download Telegram file_id for Discord moderation card previews.
    telegram_bot: Any | None = None

    def guild_config(self, guild_id: int | str | None) -> GuildConfig | None:
        if guild_id is None:
            return None
        return self.services.guilds.get(str(guild_id))

    def is_platform_admin(self, user_id: int | str) -> bool:
        return self.services.admins.can_manage(Platform.discord, str(user_id))

    def is_blocked(self, user_id: int | str) -> bool:
        return self.services.blacklist.is_blocked(
            Platform.discord, str(user_id)
        )

    def mirrors(self, source: Source) -> bool:
        return source in self.mirror_sources
