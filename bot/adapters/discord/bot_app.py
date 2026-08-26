"""discord.py client factory and the `start_discord` entry point."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from bot.adapters.discord.admin_commands import register_discord_admin
from bot.adapters.discord.context import (
    BridgeServices,
    DiscordContext,
    NotifyHook,
    PublishHook,
    resolve_services,
)
from bot.adapters.discord.event_sync import DiscordEventSync
from bot.adapters.discord.guild_decorate import GuildDecorateCog
from bot.adapters.discord.guild_setup import GuildSetupCog
from bot.adapters.discord.mirror import ChannelMirrorService, MirrorCog
from bot.adapters.discord.moderation import restore_moderation_views
from bot.adapters.discord.pass_request import PassCog
from bot.adapters.discord.pass_rooms import PassRoomsCog
from bot.adapters.discord.suggest import SuggestCog
from bot.core.models import Source
from bot.core.pass_config import load_pass_config
from bot.core.pass_service import PassService

logger = logging.getLogger(__name__)

BotReadyHook = Callable[[discord.Client], Awaitable[None]]


class RuExtrasTranslator(app_commands.Translator):
    """Use locale_str(..., ru='...') as Discord Russian name/description."""

    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,  # type: ignore[type-arg]
    ) -> str | None:
        if locale is discord.Locale.russian:
            ru = string.extras.get("ru")
            if isinstance(ru, str) and ru:
                return ru
        return None


def default_intents() -> discord.Intents:
    """message_content for the suggest channel, members for role checks."""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.guilds = True
    return intents


class SuggestBot(commands.Bot):
    """Multi-guild bot: suggest channel listener, slash commands, mod cards."""

    def __init__(
        self,
        ctx: DiscordContext,
        *,
        intents: discord.Intents | None = None,
        sync_commands: bool = True,
        on_bot_ready: BotReadyHook | None = None,
        mirror: ChannelMirrorService | None = None,
    ) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents or default_intents(),
            help_command=None,
        )
        self.ctx = ctx
        self.sync_commands = sync_commands
        self.on_bot_ready_hook = on_bot_ready
        self.mirror = mirror
        self.event_sync = DiscordEventSync(self, ctx)
        self._slash_guilds_synced: set[int] = set()
        self._slash_publish_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        await self.add_cog(SuggestCog(self, self.ctx))
        await self.add_cog(GuildSetupCog(self, self.ctx))
        await self.add_cog(GuildDecorateCog(self, self.ctx))
        db = getattr(self.ctx.services.submissions, "db", None)
        if db is not None:
            pass_config = load_pass_config()
            await self.add_cog(
                PassCog(self, self.ctx, PassService(db, pass_config))
            )
            await self.add_cog(
                PassRoomsCog(self, self.ctx, db, pass_config)
            )
        if self.mirror is not None:
            self.mirror.bind_discord(self)
            await self.add_cog(MirrorCog(self, self.mirror))
        register_discord_admin(self, self.ctx.services)
        from bot.adapters.discord.host_panel import register_discord_host

        register_discord_host(self, self.ctx.services)
        self.event_sync.register()
        await self.tree.set_translator(RuExtrasTranslator())

        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            logger.exception(
                "Slash command failed: %s",
                getattr(interaction.command, "qualified_name", "?"),
                exc_info=error,
            )
            text = "Команда не выполнилась. Подробности в data/bot-run.log."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(text, ephemeral=True)
                else:
                    await interaction.response.send_message(text, ephemeral=True)
            except discord.HTTPException:
                pass

        restored = await restore_moderation_views(self, self.ctx)
        if restored:
            logger.info("Восстановлено карточек модерации: %s", restored)

        if self.sync_commands:
            try:
                commands_synced = await self.tree.sync()
                logger.info(
                    "Слэш-команды (global) синхронизированы: %s [%s]",
                    len(commands_synced),
                    ", ".join(cmd.name for cmd in commands_synced),
                )
            except discord.HTTPException:
                logger.exception("Не удалось синхронизировать слэш-команды")

    async def close(self) -> None:
        self.event_sync.unregister()
        await super().close()

    async def on_ready(self) -> None:
        logger.info(
            "Discord-адаптер готов: %s, серверов: %s",
            self.user,
            len(self.guilds),
        )
        await self._sync_guild_channels()
        await self._harden_suggest_channels()
        if self.sync_commands:
            from bot.adapters.discord.host_panel import ensure_mod_host_panels

            try:
                n_new = await self._publish_slash_to_guilds()
                logger.info(
                    "Slash on guilds: %s ready (%s newly published)",
                    len(self._slash_guilds_synced),
                    n_new,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Guild slash publish failed")
            try:
                panels = await ensure_mod_host_panels(self, self.ctx.services)
                logger.info("Host panels in mod channels: %s", panels)
            except Exception:  # noqa: BLE001
                logger.exception("Host panel publish failed")
        if self.on_bot_ready_hook is not None:
            try:
                await self.on_bot_ready_hook(self)
            except Exception:  # noqa: BLE001
                logger.exception("on_bot_ready hook failed")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.sync_commands:
            await self._publish_slash_to_guild(guild)

    async def on_guild_available(self, guild: discord.Guild) -> None:
        if self.sync_commands:
            await self._publish_slash_to_guild(guild)

    async def _publish_slash_to_guild(self, guild: discord.Guild) -> bool:
        """Copy global slash commands onto a guild so they appear immediately."""
        async with self._slash_publish_lock:
            if guild.id in self._slash_guilds_synced:
                return False
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                self._slash_guilds_synced.add(guild.id)
                logger.info(
                    "Guild %s slash: %s [%s]",
                    guild.id,
                    len(synced),
                    ", ".join(cmd.name for cmd in synced),
                )
                return True
            except discord.HTTPException:
                logger.exception(
                    "Не удалось опубликовать slash для guild %s", guild.id
                )
                return False

    async def _publish_slash_to_guilds(self) -> int:
        published = 0
        for guild in list(self.guilds):
            if await self._publish_slash_to_guild(guild):
                published += 1
        return published

    async def _sync_guild_channels(self) -> None:
        """Bind suggest/publish channel ids by name when config is incomplete."""
        from bot.adapters.discord.guild_decorate import (
            is_publish_channel_name,
            is_suggest_channel_name,
            channel_slug,
        )
        from bot.adapters.discord import texts as ds_texts

        for guild in self.guilds:
            config = self.ctx.guild_config(guild.id) or self.ctx.services.guilds.get_or_default(
                str(guild.id)
            )
            suggest_id = config.suggest_channel_id
            publish_id = config.publish_channel_id
            legacy_publish: discord.TextChannel | None = None
            for channel in guild.text_channels:
                if not suggest_id and is_suggest_channel_name(channel.name):
                    suggest_id = str(channel.id)
                if is_publish_channel_name(channel.name):
                    if (
                        channel_slug(channel.name)
                        == ds_texts.SETUP_PUBLISH_CHANNEL_NAME.casefold()
                    ):
                        if not publish_id or publish_id != str(channel.id):
                            # Prefer #предложка even if config still points at legacy.
                            publish_id = str(channel.id)
                    elif legacy_publish is None:
                        legacy_publish = channel
            if not publish_id and legacy_publish is not None:
                publish_id = str(legacy_publish.id)
            if (
                suggest_id != config.suggest_channel_id
                or publish_id != config.publish_channel_id
            ):
                self.ctx.services.guilds.set_channels(
                    str(guild.id),
                    suggest_channel_id=suggest_id,
                    publish_channel_id=publish_id,
                )
                logger.info(
                    "Guild %s channels synced: suggest=%s publish=%s",
                    guild.id,
                    suggest_id,
                    publish_id,
                )

    async def _harden_suggest_channels(self) -> None:
        from bot.adapters.discord.guild_setup import harden_suggest_channel

        for guild in self.guilds:
            config = self.ctx.guild_config(guild.id)
            if config is None or not config.suggest_channel_id:
                continue
            channel = guild.get_channel(int(config.suggest_channel_id))
            if not isinstance(channel, discord.TextChannel):
                continue
            propose = None
            if config.propose_role_ids:
                try:
                    propose = guild.get_role(int(config.propose_role_ids[0]))
                except (TypeError, ValueError):
                    propose = None
            await harden_suggest_channel(channel, propose_role=propose)


def create_bot(
    services: BridgeServices,
    *,
    publish: PublishHook | None = None,
    notify_telegram_author: NotifyHook | None = None,
    mirror_sources: tuple[Source, ...] = (Source.telegram, Source.discord),
    intents: discord.Intents | None = None,
    sync_commands: bool = True,
    on_bot_ready: BotReadyHook | None = None,
    mirror: ChannelMirrorService | None = None,
    telegram_bot: object | None = None,
) -> SuggestBot:
    ctx = DiscordContext(
        services=resolve_services(services),
        publish=publish,
        notify_telegram_author=notify_telegram_author,
        mirror_sources=mirror_sources,
        telegram_bot=telegram_bot,
    )
    return SuggestBot(
        ctx,
        intents=intents,
        sync_commands=sync_commands,
        on_bot_ready=on_bot_ready,
        mirror=mirror,
    )


async def start_discord(
    token: str,
    services: BridgeServices,
    *,
    publish: PublishHook | None = None,
    notify_telegram_author: NotifyHook | None = None,
    mirror_sources: tuple[Source, ...] = (Source.telegram, Source.discord),
    intents: discord.Intents | None = None,
    sync_commands: bool = True,
    on_bot_ready: BotReadyHook | None = None,
    mirror: ChannelMirrorService | None = None,
    telegram_bot: object | None = None,
) -> None:
    """Run the Discord adapter until cancelled (entry point for Agent E)."""
    if not token or token == "REPLACE_ME":
        raise ValueError("DISCORD_TOKEN не задан")
    bot = create_bot(
        services,
        publish=publish,
        notify_telegram_author=notify_telegram_author,
        mirror_sources=mirror_sources,
        intents=intents,
        sync_commands=sync_commands,
        on_bot_ready=on_bot_ready,
        mirror=mirror,
        telegram_bot=telegram_bot,
    )
    async with bot:
        await bot.start(token)
