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
from bot.core.pass_config import (
    PassConfig,
    pass_duration_setting_key,
    pass_role_setting_key,
    resolve_pass_duration_sec,
)
from bot.core.pass_rooms_store import (
    PASS_KIND_TEXT,
    PASS_KIND_VOICE,
    PASS_MODE_HIDE,
    PASS_MODE_VISIBLE,
    normalize_pass_kind,
    normalize_pass_mode,
    upsert_pass_room,
)
from bot.core.pass_service import format_pass_duration

logger = logging.getLogger(__name__)

PassOverwrites = dict[discord.Role | discord.Member, discord.PermissionOverwrite]

MODE_CHOICES = [
    app_commands.Choice(name="скрыть", value=PASS_MODE_HIDE),
    app_commands.Choice(name="видно", value=PASS_MODE_VISIBLE),
]
KIND_CHOICES = [
    app_commands.Choice(name="текст", value=PASS_KIND_TEXT),
    app_commands.Choice(name="голос", value=PASS_KIND_VOICE),
]
DURATION_CHOICES = [
    app_commands.Choice(name="1 ч", value="3600"),
    app_commands.Choice(name="5 ч", value="18000"),
    app_commands.Choice(name="12 ч", value="43200"),
    app_commands.Choice(name="24 ч", value="86400"),
    app_commands.Choice(name="7 д", value="604800"),
    app_commands.Choice(name="безлимит", value="0"),
]


def pass_channel_overwrites(
    guild: discord.Guild,
    pass_role: discord.Role,
) -> PassOverwrites:
    """@everyone cannot see the room; pass role and the bot can (hide + text)."""
    return pass_room_overwrites(
        guild, pass_role, mode=PASS_MODE_HIDE, kind=PASS_KIND_TEXT
    )


def pass_room_overwrites(
    guild: discord.Guild,
    pass_role: discord.Role,
    *,
    mode: str,
    kind: str,
) -> PassOverwrites:
    """Build channel ACL for hide/visible text rooms or voice rooms."""
    mode_n = normalize_pass_mode(mode)
    kind_n = normalize_pass_kind(kind)
    overwrites: PassOverwrites = {}

    if kind_n == PASS_KIND_VOICE:
        overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True,
            connect=False,
        )
        overwrites[pass_role] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
        )
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                manage_channels=True,
                manage_permissions=True,
                move_members=True,
            )
        return overwrites

    if mode_n == PASS_MODE_VISIBLE:
        overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
        overwrites[pass_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        )
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

    overwrites[guild.default_role] = discord.PermissionOverwrite(
        view_channel=False,
        send_messages=False,
    )
    overwrites[pass_role] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
    )
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


def find_voice_channel(
    guild: discord.Guild,
    name: str,
    *,
    category: discord.CategoryChannel | None = None,
) -> discord.VoiceChannel | None:
    wanted = channel_slug(name)
    if not wanted:
        return None
    for channel in guild.voice_channels:
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


def kind_for_channel(channel: discord.abc.GuildChannel) -> str:
    if isinstance(channel, discord.VoiceChannel):
        return PASS_KIND_VOICE
    return PASS_KIND_TEXT


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

    def resolve_admin_role_ids(self, guild_id: str) -> list[str]:
        """Guild `admin_role_ids` from /admin_roles (empty if unset)."""
        config = self.ctx.guild_config(guild_id)
        if config is None:
            return []
        return list(config.admin_role_ids)

    def may_setup(
        self,
        user: discord.abc.User,
        guild: discord.Guild,
        *,
        admin_role_ids: list[str] | None = None,
    ) -> bool:
        """Platform admins, guild owner, or holders of admin_role_ids (not Manage Server alone)."""
        if guild.owner_id is not None and int(user.id) == int(guild.owner_id):
            return True
        config = self.ctx.guild_config(guild.id)
        if admin_role_ids is not None:
            return permissions.can_bot_admin(
                permissions.member_role_ids(user)
                if isinstance(user, discord.Member)
                else (),
                admin_role_ids,
                is_platform_admin=self.ctx.is_platform_admin(user.id),
            )
        return permissions.member_is_bot_admin(
            user,
            config,
            is_platform_admin=self.ctx.is_platform_admin(user.id),
        )

    @app_commands.command(
        name=app_commands.locale_str("setup_pass", ru="настроить_проходку"),
        description="Создать или закрыть канал ролью проходки",
    )
    @app_commands.guild_only()
    @app_commands.rename(
        channel="канал",
        name="имя",
        category="категория",
        role="роль",
        mode="режим",
        kind="тип",
    )
    @app_commands.describe(
        channel="Существующий канал: закрыть его ролью проходки",
        name="Имя нового канала, если существующий не указан",
        category="Куда положить новый канал; иначе «закрытые каналы»",
        role="Роль проходки; иначе бот найдёт или создаст",
        mode="скрыть — только проходка видит; видно — все видят, без роли сообщения удаляются",
        kind="текст или голос (для нового канала; у голоса Connect только с проходкой)",
    )
    @app_commands.choices(mode=MODE_CHOICES, kind=KIND_CHOICES)
    async def setup_pass(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.VoiceChannel | None = None,
        name: str | None = None,
        category: discord.CategoryChannel | None = None,
        role: discord.Role | None = None,
        mode: app_commands.Choice[str] | None = None,
        kind: app_commands.Choice[str] | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await keyboards.respond(interaction, texts.GUILD_ONLY)
            return
        if not self.may_setup(interaction.user, guild):
            await keyboards.respond(interaction, texts.PASS_SETUP_NO_RIGHTS)
            return
        await interaction.response.defer(ephemeral=True)

        mode_value = normalize_pass_mode(
            mode.value if mode is not None else None, default=PASS_MODE_HIDE
        )
        kind_value = normalize_pass_kind(
            kind.value if kind is not None else None, default=PASS_KIND_TEXT
        )

        try:
            pass_role = await self.ensure_pass_role(guild, role)
            self.db.set_setting(
                pass_role_setting_key(str(guild.id)), str(pass_role.id)
            )
            target: discord.abc.GuildChannel | None = channel
            created = False
            if target is None:
                channel_name = (name or self.config.channel_name).strip() or (
                    self.config.channel_name
                )
                room_category = category or await self.ensure_pass_category(
                    guild, pass_role
                )
                if kind_value == PASS_KIND_VOICE:
                    existing: discord.abc.GuildChannel | None = find_voice_channel(
                        guild, channel_name, category=room_category
                    )
                    if existing is None:
                        existing = find_voice_channel(guild, channel_name)
                else:
                    existing = find_text_channel(
                        guild, channel_name, category=room_category
                    )
                    if existing is None:
                        existing = find_text_channel(guild, channel_name)
                if existing is not None:
                    target = existing
                    kind_value = kind_for_channel(target)
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
                    overwrites = pass_room_overwrites(
                        guild, pass_role, mode=mode_value, kind=kind_value
                    )
                    if kind_value == PASS_KIND_VOICE:
                        target = await guild.create_voice_channel(
                            channel_name,
                            category=room_category,
                            overwrites=overwrites,
                            reason="Pass-gated room",
                        )
                    else:
                        target = await guild.create_text_channel(
                            channel_name,
                            category=room_category,
                            overwrites=overwrites,
                            topic=(
                                f"Канал с проходкой «{self.config.label}». "
                                "Запрос: /prohodka"
                            )[:1024],
                            reason="Pass-gated room",
                        )
                    created = True
            else:
                kind_value = kind_for_channel(target)

            overwrites = pass_room_overwrites(
                guild, pass_role, mode=mode_value, kind=kind_value
            )
            await target.edit(
                overwrites=overwrites,
                reason="Pass-gated room ACL",
            )
            upsert_pass_room(
                self.db,
                str(guild.id),
                target.id,
                mode=mode_value,
                kind=kind_value,
            )
        except discord.Forbidden:
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return
        except discord.HTTPException:
            logger.exception("setup_pass failed")
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return

        action = "создан" if created else "настроен"
        if kind_value == PASS_KIND_VOICE:
            access = f"войс виден всем; подключаются {pass_role.mention} и бот"
        elif mode_value == PASS_MODE_VISIBLE:
            access = (
                f"канал виден всем; без {pass_role.mention} сообщения удаляются"
            )
        else:
            access = f"его видят {pass_role.mention} и бот"
        await keyboards.respond(
            interaction,
            (
                f"Канал {target.mention} {action}: {access}. "
                "Участники запрашивают доступ командой /prohodka. "
                "Повторите команду с другим именем, чтобы добавить ещё канал. "
                "Роль бота должна быть выше роли проходки."
            ),
        )

    @app_commands.command(
        name=app_commands.locale_str("pass_config", ru="срок_проходки"),
        description="Срок выдачи проходки на этом сервере",
    )
    @app_commands.guild_only()
    @app_commands.rename(duration="срок")
    @app_commands.describe(duration="Пресет или безлимит; действует на новые выдачи")
    @app_commands.choices(duration=DURATION_CHOICES)
    async def pass_config(
        self,
        interaction: discord.Interaction,
        duration: app_commands.Choice[str] | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await keyboards.respond(interaction, texts.GUILD_ONLY)
            return
        if not self.may_setup(interaction.user, guild):
            await keyboards.respond(interaction, texts.PASS_SETUP_NO_RIGHTS)
            return
        guild_id = str(guild.id)
        if duration is None:
            current = resolve_pass_duration_sec(self.db, guild_id, self.config)
            await keyboards.respond(
                interaction,
                (
                    f"Текущий срок проходки: **{format_pass_duration(current)}**. "
                    "Выберите параметр «срок», чтобы изменить "
                    "(уже выданные роли не меняются)."
                ),
            )
            return
        try:
            seconds = int(duration.value)
        except (TypeError, ValueError):
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return
        if seconds < 0:
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return
        self.db.set_setting(pass_duration_setting_key(guild_id), str(seconds))
        await keyboards.respond(
            interaction,
            (
                f"Срок проходки на этом сервере: **{format_pass_duration(seconds)}**. "
                "Уже выданные роли не меняются."
            ),
        )

    async def ensure_pass_role(
        self, guild: discord.Guild, role: discord.Role | None
    ) -> discord.Role:
        resolved: discord.Role | None = None
        if role is not None:
            resolved = role
        if resolved is None:
            stored = self.db.get_setting(pass_role_setting_key(str(guild.id)))
            if stored:
                try:
                    resolved = guild.get_role(int(stored))
                except (TypeError, ValueError):
                    resolved = None
        if resolved is None and self.config.role_id:
            try:
                resolved = guild.get_role(int(self.config.role_id))
            except (TypeError, ValueError):
                resolved = None
        if resolved is None:
            resolved = find_role_by_name(guild, self.config.role_name)
        if resolved is None:
            return await guild.create_role(
                name=self.config.role_name,
                hoist=False,
                mentionable=False,
                reason="Temporary pass role",
            )
        await self._ensure_role_flags(resolved)
        return resolved

    async def _ensure_role_flags(self, role: discord.Role) -> None:
        needs_edit = bool(getattr(role, "hoist", False)) or bool(
            getattr(role, "mentionable", False)
        )
        if not needs_edit:
            return
        try:
            await role.edit(
                hoist=False,
                mentionable=False,
                reason="Pass role display flags",
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not update pass role flags for %s", role.id)

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
