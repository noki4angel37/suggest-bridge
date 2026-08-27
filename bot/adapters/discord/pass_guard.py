"""Delete non-pass messages in visible text pass rooms and warn briefly."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from bot.adapters.discord import permissions
from bot.adapters.discord.context import DiscordContext
from bot.core.db import BridgeDatabase
from bot.core.pass_config import PassConfig, resolve_pass_role_id
from bot.core.pass_rooms_store import (
    PASS_KIND_TEXT,
    PASS_MODE_VISIBLE,
    PassRoomEntry,
    get_pass_room,
)

logger = logging.getLogger(__name__)

WARNING_TTL_SEC = 5.0
PASS_GUARD_WARNING = (
    "Писать в этом канале можно только с ролью проходки. "
    "Сообщение удалено."
)


def is_pass_guard_room(entry: PassRoomEntry | None) -> bool:
    """True for registered visible text pass rooms (voice is not guarded)."""
    return (
        entry is not None
        and entry.mode == PASS_MODE_VISIBLE
        and entry.kind == PASS_KIND_TEXT
    )


def resolve_guard_channel_id(channel: object) -> int | None:
    """Parent text channel id for a message channel (text or its thread)."""
    if isinstance(channel, discord.Thread):
        parent_id = getattr(channel, "parent_id", None)
        return guard_lookup_channel_id(
            is_thread=True,
            channel_id=int(getattr(channel, "id", 0) or 0),
            parent_id=int(parent_id) if parent_id is not None else None,
        )
    if isinstance(channel, discord.TextChannel):
        return guard_lookup_channel_id(
            is_thread=False,
            channel_id=int(channel.id),
            parent_id=None,
        )
    return None


def guard_lookup_channel_id(
    *,
    is_thread: bool,
    channel_id: int,
    parent_id: int | None,
) -> int | None:
    """Map message channel → pass-room registry id (thread → parent)."""
    if is_thread:
        return int(parent_id) if parent_id is not None else None
    return int(channel_id)


def should_delete_pass_message(
    *,
    is_self_bot: bool = False,
    has_pass_role: bool = False,
    is_guild_owner: bool = False,
    is_bot_admin: bool = False,
) -> bool:
    """Whether a message in a guarded room should be deleted.

    Exempt: this bot, pass-role holders, guild owner, platform/table admins
    and holders of guild ``admin_role_ids``. Everyone else (including other
    bots and webhooks) is deleted.
    """
    if is_self_bot or has_pass_role or is_guild_owner or is_bot_admin:
        return False
    return True


class PassGuardCog(commands.Cog, name="pass_guard"):
    """Enforce pass role in visible text pass channels."""

    def __init__(
        self,
        bot: commands.Bot,
        ctx: DiscordContext,
        db: BridgeDatabase,
        config: PassConfig,
    ) -> None:
        self.bot = bot
        self.ctx = ctx
        self.db = db
        self.config = config

    def _author_flags(
        self, message: discord.Message
    ) -> tuple[bool, bool, bool, bool]:
        """Return (is_self_bot, has_pass_role, is_guild_owner, is_bot_admin)."""
        guild = message.guild
        assert guild is not None

        is_self = self.bot.user is not None and message.author.id == self.bot.user.id
        if is_self:
            return True, False, False, False

        has_pass = False
        is_owner = False
        is_admin = False

        member = message.author if isinstance(message.author, discord.Member) else None
        if member is not None:
            pass_role_id = resolve_pass_role_id(
                self.db, str(guild.id), self.config
            )
            if pass_role_id:
                has_pass = permissions.has_any_role(
                    permissions.member_role_ids(member), [pass_role_id]
                )
            if guild.owner_id is not None and int(member.id) == int(guild.owner_id):
                is_owner = True
            config = self.ctx.guild_config(guild.id)
            is_admin = permissions.member_is_bot_admin(
                member,
                config,
                is_platform_admin=self.ctx.is_platform_admin(member.id),
            )

        return is_self, has_pass, is_owner, is_admin

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None:
            return
        channel = message.channel
        parent_id = resolve_guard_channel_id(channel)
        if parent_id is None:
            return

        room = get_pass_room(self.db, str(guild.id), parent_id)
        if not is_pass_guard_room(room):
            return

        is_self, has_pass, is_owner, is_admin = self._author_flags(message)
        if not should_delete_pass_message(
            is_self_bot=is_self,
            has_pass_role=has_pass,
            is_guild_owner=is_owner,
            is_bot_admin=is_admin,
        ):
            return

        me = guild.me
        perms_channel = channel
        if me is None or not perms_channel.permissions_for(me).manage_messages:
            logger.warning(
                "Pass guard: no Manage Messages in channel %s (guild %s)",
                getattr(channel, "id", parent_id),
                guild.id,
            )
            return

        try:
            await message.delete()
        except discord.NotFound:
            return
        except discord.Forbidden:
            logger.warning(
                "Pass guard: Forbidden deleting message %s in channel %s",
                message.id,
                getattr(channel, "id", parent_id),
            )
            return
        except discord.HTTPException as exc:
            logger.warning(
                "Pass guard: failed to delete message %s: %s",
                message.id,
                exc,
            )
            return

        from bot.adapters.action_log import log_bot_action

        log_bot_action(
            f"Страж проходки: удалил сообщение #{message.id}",
            action="pass.guard_delete",
            actor=f"ds:{message.author.id}" if message.author else None,
            channel_id=str(parent_id),
            channel_name=getattr(channel, "name", None),
        )

        try:
            warning = await channel.send(PASS_GUARD_WARNING)
        except discord.Forbidden:
            logger.warning(
                "Pass guard: Forbidden sending warning in channel %s",
                getattr(channel, "id", parent_id),
            )
            return
        except discord.HTTPException as exc:
            logger.warning(
                "Pass guard: failed to send warning in channel %s: %s",
                getattr(channel, "id", parent_id),
                exc,
            )
            return

        await asyncio.sleep(WARNING_TTL_SEC)
        try:
            await warning.delete()
        except discord.NotFound:
            return
        except discord.Forbidden:
            logger.warning(
                "Pass guard: Forbidden deleting warning %s in channel %s",
                warning.id,
                getattr(channel, "id", parent_id),
            )
        except discord.HTTPException as exc:
            logger.warning(
                "Pass guard: failed to delete warning %s: %s",
                warning.id,
                exc,
            )
