"""Declarative guild layout: emoji┃names, categories, announce-channel ACL."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from bot.adapters.discord import keyboards, permissions, texts
from bot.adapters.discord.context import DiscordContext
from bot.adapters.discord.guild_setup import (
    harden_publish_channel,
    place_in_category,
    publish_channel_overwrites,
)

logger = logging.getLogger(__name__)

NAME_SEP = "┃"
_SEP_RE = re.compile(r"[┃│|]")


@dataclass(frozen=True)
class CategorySpec:
    key: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelSpec:
    slug: str
    emoji: str
    category_key: str | None = None
    readonly: bool = False
    aliases: tuple[str, ...] = ()


CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec(
        key="info",
        name=f"📢{NAME_SEP}ИНФО",
        aliases=("server logs🖥️", "server logs", "инфо", "ИНФО"),
    ),
    CategorySpec(
        key="base",
        name="ОСНОВА ОСНОВ (БАЗА)",
        aliases=("основа основ (база)",),
    ),
    CategorySpec(
        key="suggest",
        name=texts.SETUP_CATEGORY_NAME,
        aliases=("ПЕДЛОЖКИ", "педложки", "предложки"),
    ),
)

CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec(
        "новости-сервера",
        "📰",
        category_key="info",
        readonly=True,
        aliases=("новости",),
    ),
    ChannelSpec(
        "правила-сервера",
        "📜",
        category_key="info",
        readonly=True,
        aliases=("правила",),
    ),
    ChannelSpec(
        "бэклог-правил",
        "🗄️",
        category_key="info",
        readonly=True,
        aliases=("бэклог",),
    ),
    ChannelSpec("ивенты", "🎫", category_key="info", readonly=True),
    ChannelSpec("general", "💬", category_key="base"),
    ChannelSpec("clips-and-highlights", "🎬", category_key="base"),
    ChannelSpec("нейросеть", "🤖", category_key="base"),
    ChannelSpec(
        texts.SETUP_SUGGEST_CHANNEL_NAME,
        "💡",
        category_key="suggest",
    ),
    ChannelSpec(
        texts.SETUP_MOD_CHANNEL_NAME,
        "🛡️",
        category_key="suggest",
        aliases=("модерация",),
    ),
    # Feed: approve + TG mirror. Not the submission inbox.
    ChannelSpec(
        texts.SETUP_PUBLISH_CHANNEL_NAME,
        "📨",
        category_key="suggest",
        readonly=True,
    ),
    # Legacy leftover after rebind — decorate only.
    ChannelSpec("посты-опубликованно", "📦", category_key="suggest"),
    ChannelSpec("анон-чат", "🎭"),
    ChannelSpec("анон-чат-админ", "🔐"),
    ChannelSpec("администратоство", "🛠️"),
    ChannelSpec("2ч", "📁"),
    ChannelSpec("тераристы", "🔊"),
    ChannelSpec("afk", "💤"),
    ChannelSpec("войс без валеры", "🎤"),
    ChannelSpec("стрим пытка", "📺"),
)

_DEFAULT_TEXT_EMOJI = "💬"
_DEFAULT_VOICE_EMOJI = "🔊"
_DEFAULT_FORUM_EMOJI = "📁"


def channel_slug(name: str) -> str:
    """Strip decorative `emoji┃` prefixes; return casefolded slug."""
    raw = (name or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in _SEP_RE.split(raw) if p.strip()]
    if not parts:
        return ""
    return parts[-1].casefold()


def matches_slug(name: str, *slugs: str) -> bool:
    slug = channel_slug(name)
    return slug in {s.casefold() for s in slugs if s}


def is_publish_channel_name(name: str) -> bool:
    return matches_slug(
        name,
        texts.SETUP_PUBLISH_CHANNEL_NAME,
        *texts.SETUP_PUBLISH_CHANNEL_ALIASES,
    )


def is_suggest_channel_name(name: str) -> bool:
    """Submission inbox only — never the publish feed `#предложка`."""
    return matches_slug(name, texts.SETUP_SUGGEST_CHANNEL_NAME)


def is_mod_channel_name(name: str) -> bool:
    return matches_slug(
        name, texts.SETUP_MOD_CHANNEL_NAME, "модерация-предложки", "модерация"
    )


def decorated_name(emoji: str, slug: str) -> str:
    return f"{emoji}{NAME_SEP}{slug}"


def _spec_match_keys(spec: ChannelSpec) -> set[str]:
    return {spec.slug.casefold(), *(a.casefold() for a in spec.aliases)}


def spec_for_channel_name(name: str) -> ChannelSpec | None:
    slug = channel_slug(name)
    for spec in CHANNELS:
        if slug in _spec_match_keys(spec):
            return spec
    return None


def readonly_announce_overwrites(
    guild: discord.Guild,
    editor_role: discord.Role | None,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Everyone reads; only editor role + bot may send."""
    return publish_channel_overwrites(guild, editor_role)


async def find_or_create_category(
    guild: discord.Guild, spec: CategorySpec
) -> discord.CategoryChannel:
    aliases = {spec.name.casefold(), *(a.casefold() for a in spec.aliases)}
    alias_slugs = {channel_slug(a) or a.casefold() for a in (spec.name, *spec.aliases)}
    for category in guild.categories:
        cat_slug = channel_slug(category.name) or category.name.casefold()
        if category.name.casefold() in aliases or cat_slug in alias_slugs:
            if category.name != spec.name:
                try:
                    await category.edit(
                        name=spec.name, reason="Оформление сервера"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        "Не удалось переименовать категорию %s", category.id
                    )
            return category
    return await guild.create_category(
        spec.name, reason="Оформление сервера: категория"
    )


def find_editor_role(guild: discord.Guild) -> discord.Role | None:
    for role in guild.roles:
        if role.name == texts.SETUP_EDITOR_ROLE_NAME:
            return role
    return None


@dataclass
class DecorateResult:
    renamed: int = 0
    moved: int = 0
    locked: int = 0
    publish: discord.TextChannel | None = None
    errors: list[str] = field(default_factory=list)


async def _ensure_editor_role(guild: discord.Guild) -> discord.Role | None:
    existing = find_editor_role(guild)
    if existing is not None:
        return existing
    try:
        return await guild.create_role(
            name=texts.SETUP_EDITOR_ROLE_NAME,
            reason="Оформление: редакторы ленты",
            mentionable=True,
        )
    except (discord.Forbidden, discord.HTTPException):
        return None


async def apply_guild_layout(
    guild: discord.Guild,
    *,
    editor_role: discord.Role | None = None,
) -> DecorateResult:
    """Rename/move/lock channels per CHANNELS; return `#предложка` if found."""
    result = DecorateResult()
    editor = editor_role or await _ensure_editor_role(guild)
    if editor is None:
        result.errors.append("не удалось создать роль недоадмин")

    categories: dict[str, discord.CategoryChannel] = {}
    for cat_spec in CATEGORIES:
        try:
            categories[cat_spec.key] = await find_or_create_category(
                guild, cat_spec
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            result.errors.append(f"категория {cat_spec.key}: {exc}")

    # Map slug -> channel (first wins); refresh after renames via id.
    by_slug: dict[str, discord.abc.GuildChannel] = {}
    for channel in guild.channels:
        if isinstance(channel, discord.CategoryChannel):
            continue
        slug = channel_slug(channel.name)
        if slug and slug not in by_slug:
            by_slug[slug] = channel

    for index, spec in enumerate(CHANNELS):
        channel = None
        for key in _spec_match_keys(spec):
            channel = by_slug.get(key)
            if channel is not None:
                break
        if channel is None:
            continue

        target_name = decorated_name(spec.emoji, spec.slug)
        if channel.name != target_name:
            try:
                await channel.edit(name=target_name, reason="Оформление сервера")
                result.renamed += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                result.errors.append(f"rename {spec.slug}: {exc}")

        if spec.category_key and spec.category_key in categories:
            category = categories[spec.category_key]
            if channel.category_id != category.id:
                try:
                    await channel.edit(
                        category=category, reason="Оформление: категория"
                    )
                    result.moved += 1
                except (discord.Forbidden, discord.HTTPException) as exc:
                    result.errors.append(f"move {spec.slug}: {exc}")
            try:
                await channel.edit(
                    position=index, reason="Оформление: порядок"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        if spec.readonly and isinstance(channel, discord.TextChannel):
            try:
                await channel.edit(
                    overwrites=publish_channel_overwrites(guild, editor),
                    reason="Оформление: только недоадмин + бот",
                )
                result.locked += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                result.errors.append(f"lock {spec.slug}: {exc}")

        if (
            isinstance(channel, discord.TextChannel)
            and spec.slug == texts.SETUP_PUBLISH_CHANNEL_NAME
        ):
            result.publish = channel

    # Default emoji for anything still undecorated.
    for channel in list(guild.channels):
        if isinstance(channel, discord.CategoryChannel):
            continue
        if NAME_SEP in channel.name or "│" in channel.name:
            continue
        slug = channel_slug(channel.name) or channel.name
        if isinstance(channel, discord.VoiceChannel):
            emoji = _DEFAULT_VOICE_EMOJI
        elif isinstance(channel, discord.ForumChannel):
            emoji = _DEFAULT_FORUM_EMOJI
        elif isinstance(channel, discord.TextChannel):
            emoji = _DEFAULT_TEXT_EMOJI
        else:
            continue
        target = decorated_name(emoji, slug)
        if channel.name == target:
            continue
        try:
            await channel.edit(name=target, reason="Оформление сервера")
            result.renamed += 1
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("skip decorate %s", channel.id)

    if result.publish is None:
        for channel in guild.text_channels:
            if matches_slug(channel.name, texts.SETUP_PUBLISH_CHANNEL_NAME):
                result.publish = channel
                break

    for category in list(guild.categories):
        empty = not category.channels
        slug = channel_slug(category.name) or category.name.casefold()
        if empty and slug in {"педложки", "server logs🖥️", "server logs"}:
            try:
                await category.delete(reason="Оформление: пустая категория")
            except (discord.Forbidden, discord.HTTPException):
                pass

    return result


class GuildDecorateCog(commands.Cog, name="decorate"):
    def __init__(self, bot: commands.Bot, ctx: DiscordContext) -> None:
        self.bot = bot
        self.ctx = ctx

    def may_decorate(self, user: discord.abc.User) -> bool:
        return permissions.can_setup(
            is_platform_admin=self.ctx.is_platform_admin(user.id),
            is_guild_admin=permissions.is_guild_admin(user),
        )

    @app_commands.command(
        name="decorate_server",
        description="Оформить каналы: emoji┃имена, категории, права ленты",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def decorate_server(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await keyboards.respond(interaction, texts.GUILD_ONLY)
            return
        if not self.may_decorate(interaction.user):
            await keyboards.respond(interaction, texts.SETUP_NO_RIGHTS)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await apply_guild_layout(guild)
        except discord.Forbidden:
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return
        except discord.HTTPException:
            logger.exception("decorate_server failed")
            await keyboards.respond(interaction, texts.SETUP_FORBIDDEN)
            return

        publish = result.publish
        if publish is not None:
            editor = find_editor_role(guild)
            suggest_cat = await find_or_create_category(
                guild,
                next(c for c in CATEGORIES if c.key == "suggest"),
            )
            await place_in_category(publish, suggest_cat)
            await harden_publish_channel(publish, editor_role=editor)
            self.ctx.services.guilds.set_channels(
                str(guild.id),
                publish_channel_id=str(publish.id),
            )

        await keyboards.respond(
            interaction,
            texts.decorate_done(
                renamed=result.renamed,
                moved=result.moved,
                locked=result.locked,
                publish_mention=(
                    publish.mention if publish is not None else texts.NOT_CONFIGURED
                ),
                errors=result.errors,
            ),
        )
