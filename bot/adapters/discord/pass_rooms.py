"""Create Discord channels gated by the temporary pass role."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.adapters.discord import keyboards, permissions, texts
from bot.adapters.discord.context import DiscordContext
from bot.adapters.discord.guild_decorate import channel_slug
from bot.core.db import BridgeDatabase
from bot.core.pass_config import PassConfig, pass_role_setting_key

logger = logging.getLogger(__name__)

PassOverwrites = dict[discord.Role | discord.Member, discord.PermissionOverwrite]


def pass_channel_overwrites(
    guild: discord.Guild,
    pass_role: discord.Role,
) -> PassOverwrites:
    """@everyone cannot see the room; pass role and the bot can."""
    overwrites: PassOverwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
        ),
        pass_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            embed_links=True,
            attach_files=True,
        )
    return overwrites


def find_role_by_name(guild: discord.Guild, name: str) -> discord.Role | None:
    wanted = name.casefold().strip()
    if not wanted:
        return None
    for role in guild.roles:
        if role.name.casefold() == wanted:
            return role
    return None


def find_text_channel(
    guild: discord.Guild,
    name: str,
    *,
    category: discord.CategoryChannel | None = None,
) -> discord.TextChannel | None:
    wanted = channel_slug(name)
    if not wanted:
        return None
    for channel in guild.text_channels:
        if channel_slug(channel.name) != wanted:
            continue
        if category is not None and channel.category_id != category.id:
            continue
        return channel
    return None


def find_category(guild: discord.Guild, name: str) -> discord.CategoryChannel | None:
    wanted = channel_slug(name)
    if not wanted:
        return None
    for category in guild.categories:
        if channel_slug(category.name) == wanted:
            return category
    return None


class PassRoomsCog(commands.Cog, name="pass_rooms"):
    """Guild admin: create or lock channels behind the pass role."""

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

    def may_setup(self, user: discord.abc.User) -> bool:
        return permissions.can_setup(
            is_platform_admin=self.ctx.is_platform_admin(user.id),
            is_guild_admin=permissions.is_guild_admin(user),
        )

    @app_commands.command(
        name=app_commands.locale_str("setup_pass", ru="настроить_проходку"),
        description="Создать или закрыть канал ролью проходки",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(
        channel="канал",
        name="имя",
        category="категория",
        role="роль",
    )
    @app_commands.describe(
        channel="Существующий канал: закрыть его ролью проходки",
        name="Имя нового канала, если существующий не указан",
        category="Куда положить новый канал; иначе «закрытые каналы»",
        role="Роль проходки; иначе бот найдёт или создаст",
    )
    async def setup_pass(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        name: str | None = None,
        category: discord.CategoryChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await keyboards.respond(interaction, texts.GUILD_ONLY)
            return
        if not self.may_setup(interaction.user):
            await keyboards.respond(interaction, texts.SETUP_NO_RIGHTS)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            pass_role = await self.ensure_pass_role(guild, role)
            self.db.set_setting(
                pass_role_setting_key(str(guild.id)), str(pass_role.id)
            )
            target = channel
            created = False
            if target is None:
                channel_name = (name or self.config.channel_name).strip() or (
                    self.config.channel_name
                )
                room_category = category or await self.ensure_pass_category(
                    guild, pass_role
                )
                existing = find_text_channel(
                    guild, channel_name, category=room_category
                )
                if existing is None:
                    existing = find_text_channel(guild, channel_name)
                if existing is not None:
                    target = existing
                    if (
                        room_category is not None
                        and target.category_id != room_category.id
                    ):
                        try:
                            await target.edit(
                                category=room_category,
                                reason="Pass-gated room",
                            )
                        except (discord.Forbidden, discord.HTTPException):
                            logger.warning(
                                "Could not move pass channel %s", target.id
                            )
                else:
                    target = await guild.create_text_channel(
                        channel_name,
                        category=room_category,
                        overwrites=pass_channel_overwrites(guild, pass_role),
                        topic=(
                            f"Канал с проходкой «{self.config.label}». "
                            "Запрос: /prohodka"
                        )[:1024],
                        reason="Pass-gated room",
                    )
                    created = True
            await target.edit(
                overwrites=pass_channel_overwrites(guild, pass_role),
                reason="Pass-gated room ACL",
            )
        except discord.Forbidden:
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return
        except discord.HTTPException:
            logger.exception("setup_pass failed")
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return

        action = "создан" if created else "закрыт"
        await keyboards.respond(
            interaction,
            (
                f"Канал {target.mention} {action}: его видят "
                f"{pass_role.mention} и бот. "
                "Участники запрашивают доступ командой /prohodka. "
                "Повторите команду с другим именем, чтобы добавить ещё канал. "
                "Роль бота должна быть выше роли проходки."
            ),
        )

    async def ensure_pass_role(
        self, guild: discord.Guild, role: discord.Role | None
    ) -> discord.Role:
        if role is not None:
            return role
        stored = self.db.get_setting(pass_role_setting_key(str(guild.id)))
        if stored:
            try:
                existing = guild.get_role(int(stored))
            except (TypeError, ValueError):
                existing = None
            if existing is not None:
                return existing
        if self.config.role_id:
            try:
                existing = guild.get_role(int(self.config.role_id))
            except (TypeError, ValueError):
                existing = None
            if existing is not None:
                return existing
        named = find_role_by_name(guild, self.config.role_name)
        if named is not None:
            return named
        return await guild.create_role(
            name=self.config.role_name,
            mentionable=False,
            reason="Temporary pass role",
        )

    async def ensure_pass_category(
        self, guild: discord.Guild, pass_role: discord.Role
    ) -> discord.CategoryChannel:
        existing = find_category(guild, self.config.category_name)
        if existing is not None:
            return existing
        return await guild.create_category(
            self.config.category_name,
            overwrites=pass_channel_overwrites(guild, pass_role),
            reason="Pass-gated rooms",
        )
