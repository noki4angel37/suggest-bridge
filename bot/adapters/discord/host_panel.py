"""Discord /host panel — priority UI for admins in #модерация-предложки.

Slash commands are synced per-guild (instant). A sticky panel with buttons is
posted/updated in each configured mod channel on bot ready.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

from bot.adapters.admin_common import resolve_admin_services
from bot.adapters.discord import permissions
from bot.adapters.discord.context import DiscordContext
from bot.core import Platform
from bot.core.host_control import (
    HostControlError,
    INSTALL_HINT,
    accept_request,
    append_audit,
    cancel_request,
    create_claim_request,
    entry_is_online,
    find_hosts_for_admin,
    is_owner_discord,
    list_pending,
    owner_force_to_host,
    panel_snapshot,
    reject_request,
    require_discord_capable,
    stop_local_and_failover_owner,
)
from bot.core.host_sync import HostSyncStore

logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()

KEY_HOST_PANEL = "discord_host_panel_msg"  # guild_id:channel_id:message_id


def _may_use_host(interaction: discord.Interaction, services: Any) -> bool:
    """Bot Discord admins, owner, or guild moderators (@недоадмин / Manage Server)."""
    uid = str(interaction.user.id)
    resolved = resolve_admin_services(services)
    if is_owner_discord(uid) or resolved.admins.is_admin(Platform.discord, uid):
        return True
    bot = interaction.client
    ctx = getattr(bot, "ctx", None)
    if isinstance(ctx, DiscordContext) and interaction.guild_id is not None:
        return permissions.member_can_moderate(
            interaction.user,
            ctx.guild_config(interaction.guild_id),
            is_platform_admin=ctx.is_platform_admin(interaction.user.id),
        )
    return False


def _panel_text(db: Any, sync: HostSyncStore | None = None) -> str:
    store = sync or HostSyncStore()
    snap = panel_snapshot(db, store)
    lines = [snap.format_msk()]
    pending = list_pending(db)
    if pending:
        lines.append("")
        lines.append("Pending (id для /host_accept|/host_reject):")
        for req in pending[:12]:
            lines.append(
                f"• `{req.id}` [{req.kind}] {req.from_admin} → {req.to_host}"
            )
    lines.append("")
    lines.append("Команды: `/host` · `/host_accept` · `/host_reject` · `/host_cancel`")
    return "\n".join(lines)[:3900]


class HostPanelView(discord.ui.View):
    """Shared sticky panel — any moderator/admin can press buttons."""

    def __init__(self, services: Any) -> None:
        super().__init__(timeout=None)
        self.services = services

    def _db(self):
        return resolve_admin_services(self.services).admins.db

    def _sync(self) -> HostSyncStore:
        return HostSyncStore()

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if _may_use_host(interaction, self.services):
            return True
        await interaction.response.send_message(
            "Нужны права админа бота или модератора предложки.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Запросить primary себе",
        style=discord.ButtonStyle.primary,
        custom_id="host:claim",
        row=0,
    )
    async def claim(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        uid = str(interaction.user.id)
        sync = self._sync()
        hosts = find_hosts_for_admin(sync, discord_id=uid)
        online = [h for h in hosts if entry_is_online(h)]
        if not online:
            await interaction.response.send_message(INSTALL_HINT, ephemeral=True)
            return
        try:
            target = require_discord_capable(online[0])
            admin_key = target.admin_telegram_id or uid
            req = create_claim_request(
                self._db(), sync, admin_id=admin_key, target_host=target.host_id
            )
            await interaction.response.send_message(
                f"Запрос создан: `{req.id}` → {target.host_id}",
                ephemeral=True,
            )
            await _audit(interaction, self.services, f"claim `{req.id[:8]}…` → {target.host_id}")
            await _refresh_interaction_message(interaction, self.services)
        except HostControlError as exc:
            await interaction.response.send_message(str(exc)[:1800], ephemeral=True)

    @discord.ui.button(
        label="Принять последний",
        style=discord.ButtonStyle.success,
        custom_id="host:accept_last",
        row=0,
    )
    async def accept_last(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        pending = list_pending(self._db())
        if not pending:
            await interaction.response.send_message("Нет pending-запросов.", ephemeral=True)
            return
        req = pending[-1]
        try:
            accept_request(
                self._db(),
                self._sync(),
                request_id=req.id,
                actor=f"ds:{interaction.user.id}",
            )
            await interaction.response.send_message(
                f"Принято `{req.id[:8]}…` — prepare на {req.to_host}",
                ephemeral=True,
            )
            await _audit(interaction, self.services, f"accept `{req.id[:8]}…`")
            await _refresh_interaction_message(interaction, self.services)
        except HostControlError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @discord.ui.button(
        label="Отклонить последний",
        style=discord.ButtonStyle.secondary,
        custom_id="host:reject_last",
        row=0,
    )
    async def reject_last(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        pending = list_pending(self._db())
        if not pending:
            await interaction.response.send_message("Нет pending-запросов.", ephemeral=True)
            return
        req = pending[-1]
        try:
            reject_request(
                self._db(),
                self._sync(),
                request_id=req.id,
                actor=f"ds:{interaction.user.id}",
            )
            await interaction.response.send_message(
                f"Отклонено `{req.id[:8]}…`", ephemeral=True
            )
            await _audit(interaction, self.services, f"reject `{req.id[:8]}…`")
            await _refresh_interaction_message(interaction, self.services)
        except HostControlError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @discord.ui.button(
        label="Выключить на моём ПК",
        style=discord.ButtonStyle.danger,
        custom_id="host:stop_local",
        row=1,
    )
    async def stop_local(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        try:
            msg = stop_local_and_failover_owner(
                self._db(),
                self._sync(),
                actor=f"ds:{interaction.user.id}",
            )
            await interaction.response.send_message(msg, ephemeral=True)
            await _audit(
                interaction, self.services, f"stop_local ds:{interaction.user.id}"
            )
            await _refresh_interaction_message(interaction, self.services)
        except HostControlError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @discord.ui.button(
        label="Как установить агент",
        style=discord.ButtonStyle.secondary,
        custom_id="host:install",
        row=1,
    )
    async def install(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(INSTALL_HINT, ephemeral=True)

    @discord.ui.button(
        label="Обновить",
        style=discord.ButtonStyle.secondary,
        custom_id="host:refresh",
        row=1,
    )
    async def refresh(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        text = _panel_text(self._db())
        if interaction.message is not None:
            try:
                await interaction.message.edit(content=text, view=self)
            except Exception:  # noqa: BLE001
                pass
        await interaction.response.send_message("Статус обновлён.", ephemeral=True)

    @discord.ui.button(
        label="Force на мой ПК",
        style=discord.ButtonStyle.danger,
        custom_id="host:force_mine",
        row=2,
    )
    async def force_mine(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not is_owner_discord(interaction.user.id):
            await interaction.response.send_message(
                "Force только для супер-админа.", ephemeral=True
            )
            return
        sync = self._sync()
        hosts = find_hosts_for_admin(sync, discord_id=str(interaction.user.id))
        online = [h for h in hosts if entry_is_online(h) and h.has_discord]
        if not online:
            await interaction.response.send_message(INSTALL_HINT, ephemeral=True)
            return
        try:
            owner_force_to_host(
                self._db(),
                sync,
                target_host=online[0].host_id,
                actor=f"ds:{interaction.user.id}",
                confirmed=True,
            )
            await interaction.response.send_message(
                f"Force → {online[0].host_id}", ephemeral=True
            )
            await _audit(interaction, self.services, f"FORCE → {online[0].host_id}")
            await _refresh_interaction_message(interaction, self.services)
        except HostControlError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)


async def _refresh_interaction_message(
    interaction: discord.Interaction, services: Any
) -> None:
    if interaction.message is None:
        return
    try:
        db = resolve_admin_services(services).admins.db
        await interaction.message.edit(
            content=_panel_text(db), view=HostPanelView(services)
        )
    except Exception:  # noqa: BLE001
        logger.debug("host panel refresh skipped", exc_info=True)


async def _audit(interaction: discord.Interaction, services: Any, text: str) -> None:
    try:
        db = resolve_admin_services(services).admins.db
        append_audit(
            db,
            actor=f"ds:{interaction.user.id}",
            action="discord_host",
            detail=text,
        )
    except Exception:  # noqa: BLE001
        logger.warning("host audit db write failed")
    channel = interaction.channel
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(f"[host] {text}", allowed_mentions=NO_MENTIONS)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось написать audit в канал")


def register_discord_host(bot: Any, services: Any) -> list[Any]:
    """Register slash commands on the bot command tree (before sync)."""
    resolved = resolve_admin_services(services)

    async def _deny(interaction: discord.Interaction) -> bool:
        if _may_use_host(interaction, services):
            return False
        await interaction.response.send_message(
            "Нужны права админа бота или модератора предложки.",
            ephemeral=True,
        )
        return True

    @app_commands.command(
        name="host",
        description="Панель host: кто primary и передача бота между ПК",
    )
    async def host_cmd(interaction: discord.Interaction) -> None:
        if await _deny(interaction):
            return
        text = _panel_text(resolved.admins.db)
        view = HostPanelView(services)
        await interaction.response.send_message(
            text,
            view=view,
            ephemeral=False,
            allowed_mentions=NO_MENTIONS,
        )

    @app_commands.command(
        name="host_accept", description="Принять запрос передачи host"
    )
    @app_commands.describe(request_id="полный id запроса из панели /host")
    async def host_accept(
        interaction: discord.Interaction, request_id: str
    ) -> None:
        if await _deny(interaction):
            return
        try:
            accept_request(
                resolved.admins.db,
                HostSyncStore(),
                request_id=request_id.strip(),
                actor=f"ds:{interaction.user.id}",
            )
            await interaction.response.send_message(
                "Принято (prepare).", ephemeral=True
            )
            await _audit(interaction, services, f"accept `{request_id[:8]}…`")
        except HostControlError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(
        name="host_reject", description="Отклонить запрос передачи host"
    )
    @app_commands.describe(request_id="полный id запроса из панели /host")
    async def host_reject(
        interaction: discord.Interaction, request_id: str
    ) -> None:
        if await _deny(interaction):
            return
        try:
            reject_request(
                resolved.admins.db,
                HostSyncStore(),
                request_id=request_id.strip(),
                actor=f"ds:{interaction.user.id}",
            )
            await interaction.response.send_message("Отклонено.", ephemeral=True)
            await _audit(interaction, services, f"reject `{request_id[:8]}…`")
        except HostControlError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(
        name="host_cancel", description="Отменить свой запрос передачи"
    )
    @app_commands.describe(request_id="полный id запроса")
    async def host_cancel(
        interaction: discord.Interaction, request_id: str
    ) -> None:
        if await _deny(interaction):
            return
        try:
            cancel_request(
                resolved.admins.db,
                HostSyncStore(),
                request_id=request_id.strip(),
                actor=f"ds:{interaction.user.id}",
            )
            await interaction.response.send_message("Отменено.", ephemeral=True)
        except HostControlError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    commands = [host_cmd, host_accept, host_reject, host_cancel]
    for cmd in commands:
        bot.tree.add_command(cmd, override=True)

    # Persistent view for sticky panel buttons after restart.
    bot.add_view(HostPanelView(services))
    return commands


async def clear_guild_slash_overrides(bot: Any) -> int:
    """Remove per-guild slash copies so Discord shows each command once (global only).

    Earlier we synced global + guild copies of /host* — clients listed them twice.
    """
    cleared = 0
    for guild in list(bot.guilds):
        try:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            cleared += 1
            logger.info(
                "Guild %s: сброшены guild slash overrides (без дублей)",
                guild.id,
            )
        except discord.HTTPException:
            logger.exception("Не удалось очистить guild slash для %s", guild.id)
    return cleared


# Back-compat alias (old name used by bot_app)
async def sync_host_commands_to_guilds(bot: Any) -> int:
    return await clear_guild_slash_overrides(bot)


async def ensure_mod_host_panels(bot: Any, services: Any) -> int:
    """Post or refresh sticky /host panel in each guild's mod channel."""
    ctx = getattr(bot, "ctx", None)
    if ctx is None:
        return 0
    resolved = resolve_admin_services(services)
    db = resolved.admins.db
    posted = 0
    for guild in bot.guilds:
        config = ctx.guild_config(guild.id)
        if config is None or not config.mod_channel_id:
            continue
        channel = guild.get_channel(int(config.mod_channel_id))
        if not isinstance(channel, discord.TextChannel):
            try:
                channel = await bot.fetch_channel(int(config.mod_channel_id))
            except Exception:  # noqa: BLE001
                continue
        if not isinstance(channel, discord.TextChannel):
            continue
        text = _panel_text(db)
        view = HostPanelView(services)
        key = f"{KEY_HOST_PANEL}:{guild.id}"
        raw = db.get_setting(key)
        message = None
        if raw and ":" in raw:
            try:
                ch_id_s, msg_id_s = raw.split(":", 1)
                if int(ch_id_s) == channel.id:
                    message = await channel.fetch_message(int(msg_id_s))
            except Exception:  # noqa: BLE001
                message = None
        try:
            if message is not None:
                await message.edit(content=text, view=view)
            else:
                message = await channel.send(
                    text, view=view, allowed_mentions=NO_MENTIONS
                )
                db.set_setting(key, f"{channel.id}:{message.id}")
            posted += 1
            logger.info("Host panel ready in #%s (%s)", channel.name, guild.id)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to post host panel in guild %s", guild.id)
    return posted
