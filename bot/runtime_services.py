"""Typed runtime service container shared by Telegram and Discord adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.core.db import BridgeDatabase
from bot.core.events import EventBus
from bot.core.publish_router import PublishRouter
from bot.core.services import (
    AdminService,
    AntifloodService,
    BlacklistService,
    GuildConfigService,
    ModerationService,
    SubmissionService,
)


@dataclass
class RuntimeServices:
    """Explicit bridge runtime surface (replaces ``Bridge.services: Any``)."""

    db: BridgeDatabase
    bus: EventBus
    submissions: SubmissionService
    moderation: ModerationService
    admins: AdminService
    guilds: GuildConfigService | None
    antiflood: AntifloodService | None
    blacklist: BlacklistService | None
    publish_router: PublishRouter | None = None
    mirror: Any | None = None
    publisher: Any | None = None
    events: Any | None = None
    bot: Any | None = None

    @classmethod
    def from_telegram(cls, services: Any) -> RuntimeServices:
        return cls(
            db=services.admins.db,
            bus=services.bus,
            submissions=services.submissions,
            moderation=services.moderation,
            admins=services.admins,
            guilds=services.guilds,
            antiflood=services.antiflood,
            blacklist=services.blacklist,
            publish_router=getattr(services, "publish_router", None),
            mirror=getattr(services, "mirror", None),
            publisher=getattr(services, "publisher", None),
            events=getattr(services, "events", None),
            bot=getattr(services, "bot", None),
        )

    @classmethod
    def from_discord_bundle(cls, bundle: Any) -> RuntimeServices:
        return cls(
            db=bundle.db,
            bus=bundle.bus,
            submissions=bundle.submissions,
            moderation=bundle.moderation,
            admins=bundle.admins,
            guilds=bundle.guilds,
            antiflood=getattr(bundle, "antiflood", None),
            blacklist=getattr(bundle, "blacklist", None),
            publish_router=getattr(bundle, "publish_router", None),
            mirror=getattr(bundle, "mirror", None),
            publisher=getattr(bundle, "publisher", None),
        )
