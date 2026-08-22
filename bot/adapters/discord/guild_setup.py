"""Per-guild setup: /setup_suggest creates channels and saves GuildConfig."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.adapters.discord import keyboards, permissions, texts
from bot.adapters.discord.context import DiscordContext
from bot.core.models import GuildConfig

logger = logging.getLogger(__name__)


def suggest_channel_overwrites(
    guild: discord.Guild,
    propose_role: discord.Role | None = None,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Public write, but no history — submissions are deleted + drafted in DM."""
    overwrites: dict[
        discord.Role | discord.Member, discord.PermissionOverwrite
    ] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=False,
            send_messages=propose_role is None,
            attach_files=propose_role is None,
            embed_links=True,
            add_reactions=False,
        )
    }
    if propose_role is not None:
        overwrites[propose_role] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=False,
            send_messages=True,
            attach_files=True,
            embed_links=True,
        )
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            manage_messages=True,
            embed_links=True,
            attach_files=True,
        )
    return overwrites


def publish_channel_overwrites(
    guild: discord.Guild,
    editor_role: discord.Role | None,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Everyone can read; only editor role + bot may write; editor manages."""
    overwrites: dict[
        discord.Role | discord.Member, discord.PermissionOverwrite
    ] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=False,
            attach_files=False,
            embed_links=False,
            manage_messages=False,
        )
    }
    if editor_role is not None:
        overwrites[editor_role] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            manage_messages=True,
        )
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            manage_messages=True,
            embed_links=True,
            attach_files=True,
        )
    return overwrites


async def harden_suggest_channel(
    channel: discord.TextChannel,
    *,
    propose_role: discord.Role | None = None,
) -> None:
    """Apply privacy overwrites to an existing suggest channel."""
    try:
        await channel.edit(
            overwrites=suggest_channel_overwrites(channel.guild, propose_role),
            topic=texts.SETUP_INTRO[:1024],
            reason="Приватность предложки: сообщения скрываются",
        )
    except (discord.Forbidden, discord.HTTPException):
        logger.warning(
            "Не удалось обновить права канала предложки %s", channel.id
        )


async def ensure_suggest_category(
    guild: discord.Guild,
) -> discord.CategoryChannel:
    """Find or create the ПРЕДЛОЖКИ category for suggest/publish channels."""
    for category in guild.categories:
        if category.name == texts.SETUP_CATEGORY_NAME:
            return category
    return await guild.create_category(
        texts.SETUP_CATEGORY_NAME,
        reason="Категория предложки",
    )


async def place_in_category(
    channel: discord.TextChannel,
    category: discord.CategoryChannel,
    *,
    reason: str = "Перемещение в категорию предложки",
) -> None:
    if channel.category_id == category.id:
        return
    try:
        await channel.edit(category=category, reason=reason)
    except (discord.Forbidden, discord.HTTPException):
        logger.warning(
            "Не удалось переместить канал %s в категорию %s",
            channel.id,
            category.id,
        )


async def harden_publish_channel(
    channel: discord.TextChannel,
    *,
    editor_role: discord.Role | None = None,
) -> None:
    try:
        await channel.edit(
            overwrites=publish_channel_overwrites(channel.guild, editor_role),
            topic=texts.setup_publish_intro()[:1024],
            reason="Лента публикации / зеркало TG",
        )
    except (discord.Forbidden, discord.HTTPException):
        logger.warning(
            "Не удалось обновить права канала публикации %s", channel.id
        )


def channel_mention(guild: discord.Guild, channel_id: str | None) -> str:
    if not channel_id:
        return texts.NOT_CONFIGURED
    try:
        channel = guild.get_channel(int(channel_id))
    except (TypeError, ValueError):
        return texts.NOT_CONFIGURED
    return channel.mention if channel else f"#{channel_id}"


def roles_summary(
    guild: discord.Guild, role_ids: list[str], *, empty: str
) -> str:
    mentions: list[str] = []
    for role_id in role_ids:
        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            continue
        mentions.append(role.mention if role else f"<@&{role_id}>")
    return ", ".join(mentions) if mentions else empty


def rate_limit_summary(config: GuildConfig) -> str:
    if not config.rate_limit_enabled:
        return "по умолчанию"
    count = config.rate_limit_count or "?"
    window = config.rate_limit_window_sec or "?"
    return f"{count} заявок за {window} с"


class GuildSetupCog(commands.Cog, name="setup"):
    def __init__(self, bot: commands.Bot, ctx: DiscordContext) -> None:
        self.bot = bot
        self.ctx = ctx

    def may_setup(self, user: discord.abc.User) -> bool:
        return permissions.can_setup(
            is_platform_admin=self.ctx.is_platform_admin(user.id),
            is_guild_admin=permissions.is_guild_admin(user),
        )

    @app_commands.command(
        name="setup_suggest",
        description="Настроить предложку: заявки, модерация, публикация",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(
        suggest_channel="канал_заявок",
        mod_channel="канал_модерации",
        publish_channel="канал_публикации",
        propose_role="роль_для_заявок",
        mod_role="роль_модерации",
    )
    @app_commands.describe(
        suggest_channel="Канал для заявок, иначе бот создаст/переименует",
        mod_channel="Канал для карточек модерации, иначе бот создаст новый",
        publish_channel="Лента публикации / зеркало TG, иначе создаст",
        propose_role="Кто может отправлять заявки; без роли — все участники",
        mod_role="Роль модераторов предложки",
    )
    async def setup_suggest(
        self,
        interaction: discord.Interaction,
        suggest_channel: discord.TextChannel | None = None,
        mod_channel: discord.TextChannel | None = None,
        publish_channel: discord.TextChannel | None = None,
        propose_role: discord.Role | None = None,
        mod_role: discord.Role | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await keyboards.respond(interaction, texts.GUILD_ONLY)
            return
        if not self.may_setup(interaction.user):
            await keyboards.respond(interaction, texts.SETUP_NO_RIGHTS)
            return
        await interaction.response.defer(ephemeral=True)

        config = self.ctx.services.guilds.get_or_default(str(guild.id))
        try:
            editor_role = await self.ensure_editor_role(guild)
            suggest = suggest_channel or await self.ensure_suggest_channel(
                guild, config, propose_role
            )
            await self.rename_channel(
                suggest, texts.SETUP_SUGGEST_CHANNEL_NAME
            )
            moderation_channel = (
                mod_channel
                or await self.ensure_mod_channel(guild, config, mod_role)
            )
            publish = publish_channel or await self.ensure_publish_channel(
                guild, config, editor_role
            )
            category = await ensure_suggest_category(guild)
            await place_in_category(publish, category)
            await harden_publish_channel(publish, editor_role=editor_role)
        except discord.Forbidden:
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return
        except discord.HTTPException:
            logger.exception("Не удалось создать каналы предложки")
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return

        self.ctx.services.guilds.set_channels(
            str(guild.id),
            suggest_channel_id=str(suggest.id),
            mod_channel_id=str(moderation_channel.id),
            publish_channel_id=str(publish.id),
        )
        if isinstance(suggest, discord.TextChannel):
            await harden_suggest_channel(suggest, propose_role=propose_role)
        stored = self.ctx.services.guilds.set_roles(
            str(guild.id),
            propose_role_ids=(
                [str(propose_role.id)]
                if propose_role
                else config.propose_role_ids
            ),
            mod_role_ids=(
                [str(mod_role.id)] if mod_role else config.mod_role_ids
            ),
        )

        await keyboards.respond(
            interaction,
            texts.setup_done(
                suggest.mention,
                moderation_channel.mention,
                publish.mention,
                roles_summary(
                    guild, stored.propose_role_ids, empty=texts.ROLES_EVERYONE
                ),
                roles_summary(
                    guild, stored.mod_role_ids, empty=texts.NOT_CONFIGURED
                ),
            ),
        )

    @app_commands.command(
        name="setup_info", description="Показать настройки предложки"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup_info(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await keyboards.respond(interaction, texts.GUILD_ONLY)
            return
        if not self.may_setup(interaction.user):
            await keyboards.respond(interaction, texts.SETUP_NO_RIGHTS)
            return
        config = self.ctx.services.guilds.get_or_default(str(guild.id))
        await keyboards.respond(
            interaction,
            texts.setup_info(
                channel_mention(guild, config.suggest_channel_id),
                channel_mention(guild, config.mod_channel_id),
                channel_mention(guild, config.publish_channel_id),
                roles_summary(
                    guild, config.propose_role_ids, empty=texts.ROLES_EVERYONE
                ),
                roles_summary(
                    guild, config.mod_role_ids, empty=texts.NOT_CONFIGURED
                ),
                rate_limit_summary(config),
            ),
        )

    # --- channel helpers -----------------------------------------------------

    async def ensure_editor_role(self, guild: discord.Guild) -> discord.Role:
        for role in guild.roles:
            if role.name == texts.SETUP_EDITOR_ROLE_NAME:
                return role
        return await guild.create_role(
            name=texts.SETUP_EDITOR_ROLE_NAME,
            reason="Редакторы ленты публикации",
            mentionable=True,
        )

    async def rename_channel(
        self, channel: discord.TextChannel, name: str
    ) -> None:
        from bot.adapters.discord.guild_decorate import channel_slug

        if channel.name == name or channel_slug(channel.name) == channel_slug(
            name
        ):
            return
        try:
            await channel.edit(name=name, reason="Переименование предложки")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Не удалось переименовать канал %s → %s", channel.id, name
            )

    async def ensure_suggest_channel(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        propose_role: discord.Role | None,
    ) -> discord.TextChannel:
        from bot.adapters.discord.guild_decorate import is_suggest_channel_name

        existing = self.existing_channel(guild, config.suggest_channel_id)
        if existing is not None:
            await harden_suggest_channel(existing, propose_role=propose_role)
            return existing
        for channel in guild.text_channels:
            if is_suggest_channel_name(channel.name):
                await harden_suggest_channel(channel, propose_role=propose_role)
                return channel
        channel = await guild.create_text_channel(
            texts.SETUP_SUGGEST_CHANNEL_NAME,
            overwrites=suggest_channel_overwrites(guild, propose_role),
            topic=texts.SETUP_INTRO[:1024],
            reason="Настройка предложки",
        )
        await self.send_intro(channel, texts.SETUP_INTRO)
        return channel

    async def ensure_mod_channel(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        mod_role: discord.Role | None,
    ) -> discord.TextChannel:
        existing = self.existing_channel(guild, config.mod_channel_id)
        if existing is not None:
            return existing
        overwrites: dict[
            discord.Role | discord.Member, discord.PermissionOverwrite
        ] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            )
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True
            )
        roles = [mod_role] if mod_role else self.stored_mod_roles(guild, config)
        for role in roles:
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
        channel = await guild.create_text_channel(
            texts.SETUP_MOD_CHANNEL_NAME,
            overwrites=overwrites,
            topic=texts.SETUP_MOD_INTRO[:1024],
            reason="Настройка предложки",
        )
        await self.send_intro(channel, texts.SETUP_MOD_INTRO)
        return channel

    async def ensure_publish_channel(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        editor_role: discord.Role | None,
    ) -> discord.TextChannel:
        category = await ensure_suggest_category(guild)
        existing = self.existing_channel(guild, config.publish_channel_id)
        if existing is not None:
            await place_in_category(existing, category)
            await harden_publish_channel(existing, editor_role=editor_role)
            return existing
        from bot.adapters.discord.guild_decorate import is_publish_channel_name

        for channel in guild.text_channels:
            if is_publish_channel_name(channel.name):
                # Prefer exact `#предложка` over legacy `#посты-опубликованно`.
                from bot.adapters.discord.guild_decorate import channel_slug

                if channel_slug(channel.name) != texts.SETUP_PUBLISH_CHANNEL_NAME.casefold():
                    continue
                await place_in_category(channel, category)
                await harden_publish_channel(channel, editor_role=editor_role)
                return channel
        for channel in guild.text_channels:
            if is_publish_channel_name(channel.name):
                await place_in_category(channel, category)
                await harden_publish_channel(channel, editor_role=editor_role)
                return channel
        channel = await guild.create_text_channel(
            texts.SETUP_PUBLISH_CHANNEL_NAME,
            overwrites=publish_channel_overwrites(guild, editor_role),
            topic=texts.setup_publish_intro()[:1024],
            category=category,
            reason="Лента публикации предложки",
        )
        await self.send_intro(channel, texts.setup_publish_intro())
        return channel

    def stored_mod_roles(
        self, guild: discord.Guild, config: GuildConfig
    ) -> list[discord.Role | None]:
        roles: list[discord.Role | None] = []
        for role_id in config.mod_role_ids:
            try:
                roles.append(guild.get_role(int(role_id)))
            except (TypeError, ValueError):
                continue
        return roles

    def existing_channel(
        self, guild: discord.Guild, channel_id: str | None
    ) -> discord.TextChannel | None:
        if not channel_id:
            return None
        try:
            channel = guild.get_channel(int(channel_id))
        except (TypeError, ValueError):
            return None
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def send_intro(
        self, channel: discord.TextChannel, text: str
    ) -> None:
        try:
            await channel.send(text)
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("Не удалось отправить описание в %s", channel.id)
