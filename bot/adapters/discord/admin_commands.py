"""Discord slash commands for admins: admins, blacklist, roles, limits.

Two permission levels:

* bot-wide commands (admins, blacklist, antiflood) — Discord admins from the
  bridge database, or members holding any role from this guild's
  `admin_role_ids` (set by the guild owner via `/admin_roles`); in DMs only
  the admins table applies;
* guild-scoped config (`/roles_propose`, `/roles_mod`, `/ratelimit_config`) —
  bot admins (table or guild admin roles) or members with "Manage Server".

`/admin_roles` itself is guild-owner only.

Bootstrap: the first Discord admin is the `OWNER_DISCORD_ID` (seeded on
startup) or is added by a Telegram admin with `/adddiscordadmin <discord_id>`;
the first Telegram owner comes from `ADMIN_IDS` / `OWNER_TELEGRAM_ID`. Until
then, guild config is available to "Manage Server" members.

Wiring (see `register_discord_admin`):

    from bot.adapters.discord.admin_commands import register_discord_admin
    register_discord_admin(bot, services)   # before bot.tree.sync()
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import discord
from discord import app_commands

from bot.adapters.admin_common import (
    DOWNLOAD_INSTRUCTIONS,
    RATE_LIMIT_EXPLAINER,
    AdminServices,
    format_admin_added,
    format_admin_not_found,
    format_admin_removed,
    format_admins_list,
    format_antiflood_status,
    format_antiflood_updated,
    format_bad_platform,
    format_bad_user_id,
    format_bad_user_ref,
    format_block_admin_guard,
    format_blocked_added,
    format_blocked_list,
    format_last_admin_guard,
    format_not_blocked,
    format_queue_report,
    format_rate_limit_status,
    format_rate_limit_updated,
    format_roles_config,
    format_roles_updated,
    format_self_removal_guard,
    format_unblocked,
    parse_platform,
    parse_user_id,
    parse_username_tag,
    resolve_admin_services,
)
from bot.adapters.discord import permissions
from bot.core.models import GuildConfig, Platform
from bot.core.pack_dist import build_suggest_bot_zip

NO_MENTIONS = discord.AllowedMentions.none()
DENY_BOT_ADMIN = (
    "Команда только для админов бота.\n"
    "Владелец: проверьте OWNER_DISCORD_ID в local.env (должен быть ваш Discord id) "
    "и перезапустите бота.\n"
    "Или попросите Telegram-админа: /adddiscordadmin <ваш_discord_id>.\n"
    "На сервере: владелец может выдать доступ ролям через /admin_roles."
)
DENY_GUILD_ADMIN = (
    "Нужны права админа бота или «Управление сервером» на этом сервере."
)
DENY_GUILD_OWNER = "Команда только для владельца этого сервера Discord."
GUILD_ONLY = "Команда работает только на сервере Discord."
ROLE_PICKER_TIMEOUT_SEC = 180
ANTIFLOOD_HINT = (
    "Изменить: /antiflood_config с параметрами limit и window_sec."
)
RATE_LIMIT_HINT = "Изменить: /ratelimit_config."
DOWNLOAD_CAPTION = (
    "Распакуйте zip → .\\install-agent.ps1 → local.env → "
    "Syncthing suggest-host-sync → .\\run-agent.ps1 → /host"
)
logger = logging.getLogger(__name__)
PLATFORM_CHOICES = [
    app_commands.Choice(name="Telegram", value="telegram"),
    app_commands.Choice(name="Discord", value="discord"),
]


class RolePickerView(discord.ui.View):
    """Role select + reset button; empty selection is a valid answer."""

    def __init__(
        self,
        *,
        services: AdminServices,
        guild_id: str,
        field: str,
        requester_id: int,
        placeholder: str,
        kind_label: str,
    ) -> None:
        super().__init__(timeout=ROLE_PICKER_TIMEOUT_SEC)
        self.services = services
        self.guild_id = guild_id
        self.field = field
        self.requester_id = requester_id
        self.kind_label = kind_label

        select: discord.ui.RoleSelect = discord.ui.RoleSelect(
            placeholder=placeholder, min_values=0, max_values=25
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)
        self._select = select

    async def interaction_check(
        self, interaction: discord.Interaction
    ) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Это меню вызвал другой админ.", ephemeral=True
        )
        return False

    async def _on_select(self, interaction: discord.Interaction) -> None:
        roles = list(self._select.values)
        await self._save(interaction, [str(role.id) for role in roles])

    @discord.ui.button(
        label="Сбросить", style=discord.ButtonStyle.secondary, row=1
    )
    async def reset(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._save(interaction, [])

    async def _save(
        self, interaction: discord.Interaction, role_ids: list[str]
    ) -> None:
        self.services.guilds.set_roles(self.guild_id, **{self.field: role_ids})
        names = _role_names(interaction.guild, role_ids)
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        await interaction.response.edit_message(
            content=format_roles_updated(
                self.kind_label, role_ids, names=names
            ),
            view=self,
        )


def _role_names(
    guild: discord.Guild | None, role_ids: Sequence[str]
) -> dict[str, str]:
    if guild is None:
        return {}
    wanted = {str(role_id) for role_id in role_ids}
    return {
        str(role.id): role.name
        for role in guild.roles
        if str(role.id) in wanted
    }


class DiscordAdminUI:
    """Slash command bodies; thin commands in `register_discord_admin` call these."""

    def __init__(
        self, services: AdminServices, *, container: Any | None = None
    ) -> None:
        self.services = services
        self.container = container

    # --- guards --------------------------------------------------------------

    def is_bot_admin(
        self,
        user_id: int,
        *,
        guild_id: str | None = None,
        member: Any | None = None,
    ) -> bool:
        """Table admin, or (in a guild) holder of configured admin roles."""
        in_table = self.services.admins.can_manage(
            Platform.discord, str(user_id)
        )
        if guild_id is None:
            return in_table
        config = self.services.guilds.get(guild_id)
        if member is not None:
            return permissions.member_is_bot_admin(
                member, config, is_platform_admin=in_table
            )
        return permissions.can_bot_admin(
            (),
            config.admin_role_ids if config else (),
            is_platform_admin=in_table,
        )

    def _is_bot_admin_interaction(
        self, interaction: discord.Interaction
    ) -> bool:
        guild_id = (
            str(interaction.guild_id) if interaction.guild_id is not None else None
        )
        return self.is_bot_admin(
            interaction.user.id,
            guild_id=guild_id,
            member=interaction.user if guild_id is not None else None,
        )

    def _may_configure_guild(self, interaction: discord.Interaction) -> bool:
        if self._is_bot_admin_interaction(interaction):
            return True
        member = interaction.user
        perms = getattr(member, "guild_permissions", None)
        return bool(perms and (perms.manage_guild or perms.administrator))

    async def _require_bot_admin(
        self, interaction: discord.Interaction
    ) -> bool:
        if self._is_bot_admin_interaction(interaction):
            return True
        await _reply(interaction, DENY_BOT_ADMIN)
        return False

    async def _require_guild_admin(
        self, interaction: discord.Interaction
    ) -> str | None:
        if interaction.guild_id is None:
            await _reply(interaction, GUILD_ONLY)
            return None
        if not self._may_configure_guild(interaction):
            await _reply(interaction, DENY_GUILD_ADMIN)
            return None
        return str(interaction.guild_id)

    async def _require_guild_owner(
        self, interaction: discord.Interaction
    ) -> str | None:
        if interaction.guild is None or interaction.guild_id is None:
            await _reply(interaction, GUILD_ONLY)
            return None
        if interaction.guild.owner_id != interaction.user.id:
            await _reply(interaction, DENY_GUILD_OWNER)
            return None
        return str(interaction.guild_id)

    # --- admins --------------------------------------------------------------

    async def queue(self, interaction: discord.Interaction) -> None:
        if not await self._require_bot_admin(interaction):
            return
        submissions = getattr(self.container, "submissions", None)
        if submissions is None:
            await _reply(interaction, "Сервис заявок недоступен.")
            return
        pending = submissions.list_pending(limit=30)
        scheduled = submissions.list_scheduled(limit=30)
        text = format_queue_report(pending, scheduled)
        # Discord embeds have a 4096 desc limit; keep plain ephemeral text.
        if len(text) > 1900:
            text = text[:1890] + "\n…"
        await _reply(interaction, text)

    async def admins_list(self, interaction: discord.Interaction) -> None:
        if not await self._require_bot_admin(interaction):
            return
        await _reply(
            interaction, format_admins_list(self.services.admins.list_admins())
        )

    async def download_bot(self, interaction: discord.Interaction) -> None:
        """DM install instructions + zip; fall back to ephemeral if DM closed."""
        if not await self._require_bot_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            zip_path = await asyncio.to_thread(build_suggest_bot_zip)
        except Exception:  # noqa: BLE001
            logger.exception("pack suggest-bot zip failed")
            await interaction.followup.send(
                "Не удалось собрать пакет. Проверьте логи primary-ПК.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        file = discord.File(str(zip_path), filename="suggest-bot.zip")
        try:
            dm = await interaction.user.create_dm()
            await dm.send(DOWNLOAD_INSTRUCTIONS)
            await dm.send(content=DOWNLOAD_CAPTION, file=file)
            await interaction.followup.send(
                "Инструкция и zip отправлены в личку.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except (discord.Forbidden, discord.HTTPException):
            # User blocked DMs — send ephemeral attachment in the interaction.
            file = discord.File(str(zip_path), filename="suggest-bot.zip")
            await interaction.followup.send(
                DOWNLOAD_INSTRUCTIONS + "\n\n" + DOWNLOAD_CAPTION,
                file=file,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )

    async def admin_add(
        self, interaction: discord.Interaction, platform_value: str, raw_id: str
    ) -> None:
        if not await self._require_bot_admin(interaction):
            return
        target = _parse_target(platform_value, raw_id)
        if isinstance(target, str):
            await _reply(interaction, target)
            return
        platform, user_id = target

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        try:
            was_new = not self.services.admins.is_admin(platform, user_id)
            admin = await self.services.admins.add_admin(
                platform, user_id, added_by=f"ds:{interaction.user.id}"
            )
            await _reply(interaction, format_admin_added(admin, was_new=was_new))
        except Exception:  # noqa: BLE001
            logger.exception("admin_add failed platform=%s", platform_value)
            await _reply(
                interaction,
                "Не удалось добавить админа. Подробности в data/bot-run.log.",
            )

    async def admin_remove(
        self, interaction: discord.Interaction, platform_value: str, raw_id: str
    ) -> None:
        if not await self._require_bot_admin(interaction):
            return
        target = _parse_target(platform_value, raw_id)
        if isinstance(target, str):
            await _reply(interaction, target)
            return
        platform, user_id = target

        if (
            platform is Platform.discord
            and str(interaction.user.id) == user_id
        ):
            await _reply(interaction, format_self_removal_guard())
            return
        if self._is_last_admin(platform, user_id):
            await _reply(interaction, format_last_admin_guard(platform))
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        try:
            removed = await self.services.admins.remove_admin(platform, user_id)
            await _reply(
                interaction,
                format_admin_removed(platform, user_id)
                if removed
                else format_admin_not_found(platform, user_id),
            )
        except Exception:  # noqa: BLE001
            logger.exception("admin_remove failed platform=%s", platform_value)
            await _reply(
                interaction,
                "Не удалось снять права админа. Подробности в data/bot-run.log.",
            )

    def _is_last_admin(self, platform: Platform, user_id: str) -> bool:
        if platform is not Platform.telegram:
            return False
        remaining = [
            admin
            for admin in self.services.admins.list_admins(
                platform=Platform.telegram
            )
            if admin.platform_user_id != user_id
        ]
        return not remaining

    # --- blacklist -----------------------------------------------------------

    async def blocked_list(self, interaction: discord.Interaction) -> None:
        if not await self._require_bot_admin(interaction):
            return
        await _reply(
            interaction,
            format_blocked_list(self.services.blacklist.list_blocked()),
        )

    async def block_user(
        self,
        interaction: discord.Interaction,
        platform_value: str,
        raw_id: str,
        reason: str | None,
    ) -> None:
        if not await self._require_bot_admin(interaction):
            return
        target = _parse_target(platform_value, raw_id)
        if isinstance(target, str):
            await _reply(interaction, target)
            return
        platform, user_id = target

        if self.services.admins.is_admin(platform, user_id):
            await _reply(
                interaction, format_block_admin_guard(platform, user_id)
            )
            return
        entry = await self.services.blacklist.block(
            platform, user_id, reason=(reason or "").strip() or None
        )
        await _reply(interaction, format_blocked_added(entry))

    async def unblock_user(
        self, interaction: discord.Interaction, platform_value: str, raw_id: str
    ) -> None:
        if not await self._require_bot_admin(interaction):
            return
        target = _parse_target(platform_value, raw_id)
        if isinstance(target, str):
            await _reply(interaction, target)
            return
        platform, user_id = target

        removed = await self.services.blacklist.unblock(platform, user_id)
        await _reply(
            interaction,
            format_unblocked(platform, user_id)
            if removed
            else format_not_blocked(platform, user_id),
        )

    # --- guild roles ---------------------------------------------------------

    async def roles_propose(self, interaction: discord.Interaction) -> None:
        await self._roles_picker(
            interaction,
            field="propose_role_ids",
            kind_label="предлагать",
            placeholder="Роли, которые могут предлагать (пусто = все)",
        )

    async def roles_mod(self, interaction: discord.Interaction) -> None:
        await self._roles_picker(
            interaction,
            field="mod_role_ids",
            kind_label="модерировать",
            placeholder="Роли с доступом к модерации",
        )

    async def admin_roles(self, interaction: discord.Interaction) -> None:
        guild_id = await self._require_guild_owner(interaction)
        if guild_id is None:
            return
        config = self.services.guilds.get_or_default(guild_id)
        names = _role_names(interaction.guild, config.admin_role_ids)
        lines = [
            f"Роли админов бота на сервере {guild_id}:",
            "",
        ]
        if config.admin_role_ids:
            lines.extend(
                f"• {names.get(rid, rid)} ({rid})"
                if names.get(rid)
                else f"• {rid}"
                for rid in config.admin_role_ids
            )
        else:
            lines.append("• не заданы (только админы из таблицы бота)")
        lines.extend(
            (
                "",
                "Выберите роли: носители получат все админ-команды бота на "
                "этом сервере. «Сбросить» — убрать роли.",
            )
        )
        view = RolePickerView(
            services=self.services,
            guild_id=guild_id,
            field="admin_role_ids",
            requester_id=interaction.user.id,
            placeholder="Роли с правами админа бота",
            kind_label="администрировать бота",
        )
        await interaction.response.send_message(
            "\n".join(lines),
            view=view,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    async def _roles_picker(
        self,
        interaction: discord.Interaction,
        *,
        field: str,
        kind_label: str,
        placeholder: str,
    ) -> None:
        guild_id = await self._require_guild_admin(interaction)
        if guild_id is None:
            return
        config = self.services.guilds.get_or_default(guild_id)
        names = _role_names(
            interaction.guild, config.propose_role_ids + config.mod_role_ids
        )
        view = RolePickerView(
            services=self.services,
            guild_id=guild_id,
            field=field,
            requester_id=interaction.user.id,
            placeholder=placeholder,
            kind_label=kind_label,
        )
        await interaction.response.send_message(
            format_roles_config(config, propose_names=names, mod_names=names),
            view=view,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    # --- bot news ------------------------------------------------------------

    async def bot_news(
        self, interaction: discord.Interaction, text: str
    ) -> None:
        if not await self._require_bot_admin(interaction):
            return
        body = (text or "").strip()
        if not body:
            await _reply(interaction, "Нужен текст новости (3–8 строк).")
            return
        await interaction.response.defer(ephemeral=True)
        try:
            from bot.adapters.discord.bot_news import post_bot_news

            message = await post_bot_news(interaction.client, text=body)
        except Exception:  # noqa: BLE001
            logger.exception("bot_news failed")
            await _reply(
                interaction,
                "Не удалось отправить в канал изменений. "
                "Проверьте DISCORD_BOT_NEWS_CHANNEL_ID и права бота.",
            )
            return
        jump = getattr(message, "jump_url", None) or f"msg {message.id}"
        await _reply(interaction, f"Опубликовано: {jump}")

    # --- antiflood / rate limit ---------------------------------------------

    async def antiflood_config(
        self,
        interaction: discord.Interaction,
        limit: int | None,
        window_sec: int | None,
    ) -> None:
        if not await self._require_bot_admin(interaction):
            return
        antiflood = self.services.antiflood
        if limit is None and window_sec is None:
            await _reply(
                interaction,
                format_antiflood_status(
                    antiflood.limit, antiflood.window_sec, hint=ANTIFLOOD_HINT
                ),
            )
            return
        antiflood.configure_defaults(limit=limit, window_sec=window_sec)
        await _reply(
            interaction,
            format_antiflood_updated(antiflood.limit, antiflood.window_sec),
        )

    async def ratelimit_config(
        self,
        interaction: discord.Interaction,
        enabled: bool | None,
        count: int | None,
        window_sec: int | None,
    ) -> None:
        guild_id = await self._require_guild_admin(interaction)
        if guild_id is None:
            return
        if enabled is None and count is None and window_sec is None:
            config: GuildConfig = self.services.guilds.get_or_default(guild_id)
            await _reply(
                interaction,
                format_rate_limit_status(config, hint=RATE_LIMIT_HINT)
                + "\n\n"
                + RATE_LIMIT_EXPLAINER,
            )
            return

        current = self.services.guilds.get_or_default(guild_id)
        updated = self.services.guilds.set_rate_limit(
            guild_id,
            enabled=current.rate_limit_enabled if enabled is None else enabled,
            count=count,
            window_sec=window_sec,
        )
        await _reply(interaction, format_rate_limit_updated(updated))


def _parse_target(
    platform_value: str, raw_id: str
) -> tuple[Platform, str] | str:
    """Return `(platform, user_id)` or a RU error message."""
    platform = parse_platform(platform_value)
    if platform is None:
        return format_bad_platform(platform_value)
    user_id = parse_user_id(raw_id)
    if user_id is not None:
        return platform, user_id
    # Non-numeric refs must be resolved by the caller (Discord tag lookup /
    # Telegram get_chat) before reaching storage — never persist @names.
    return format_bad_user_ref(raw_id)


async def _resolve_discord_tag(
    interaction: discord.Interaction, raw: str
) -> str | None:
    """Resolve @name / name / name#discrim to Discord snowflake in this guild."""
    tag = parse_username_tag(raw) or raw.strip().lstrip("@")
    if not tag:
        return None
    guild = interaction.guild
    if guild is None:
        return None
    # Exact username match (new Discord usernames have no discriminator).
    lowered = tag.lower()
    if "#" in tag:
        name, _, disc = tag.partition("#")
        member = discord.utils.get(
            guild.members, name=name, discriminator=disc
        )
        if member is not None:
            return str(member.id)
    member = discord.utils.find(
        lambda m: (m.name or "").lower() == lowered
        or (m.display_name or "").lower() == lowered
        or (getattr(m, "global_name", None) or "").lower() == lowered,
        guild.members,
    )
    if member is not None:
        return str(member.id)
    # Query API by username if members intent incomplete
    try:
        found = await guild.query_members(query=tag.split("#")[0], limit=5)
    except Exception:  # noqa: BLE001
        return None
    for candidate in found:
        if (candidate.name or "").lower() == lowered:
            return str(candidate.id)
        if (candidate.display_name or "").lower() == lowered:
            return str(candidate.id)
    if len(found) == 1:
        return str(found[0].id)
    return None


async def _reply(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(
            text, ephemeral=True, allowed_mentions=NO_MENTIONS
        )
        return
    await interaction.response.send_message(
        text, ephemeral=True, allowed_mentions=NO_MENTIONS
    )


def register_discord_admin(bot: Any, services: Any) -> list[Any]:
    """Add admin slash commands to `bot.tree`; returns the added commands.

    Call before guild slash publish. Commands live on each guild
    (``copy_global_to`` + ``sync(guild)``); globals are cleared so the
    Discord ``/`` picker does not show duplicates. Guild-only commands
    are marked ``guild_only``.
    """
    resolved = resolve_admin_services(services)
    ui = DiscordAdminUI(resolved, container=services)

    @app_commands.command(
        name="admins_list", description="Список админов бота (TG + DS)"
    )
    async def admins_list(interaction: discord.Interaction) -> None:
        await ui.admins_list(interaction)

    @app_commands.command(
        name="queue",
        description="Очередь модерации и отложенные публикации",
    )
    async def queue(interaction: discord.Interaction) -> None:
        await ui.queue(interaction)

    @app_commands.command(
        name="download_bot",
        description="Скачать пакет бота для нового ПК (инструкция + zip в ЛС)",
    )
    async def download_bot(interaction: discord.Interaction) -> None:
        await ui.download_bot(interaction)

    @app_commands.command(name="admin_add", description="Выдать права админа")
    @app_commands.describe(
        platform="Платформа аккаунта",
        user="Упомянуть пользователя Discord (предпочтительно)",
        user_id="Или числовой id / @username",
    )
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def admin_add(
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        user: discord.User | None = None,
        user_id: str | None = None,
    ) -> None:
        if user is not None and platform.value == "discord":
            await ui.admin_add(interaction, platform.value, str(user.id))
            return
        if user is not None and platform.value == "telegram":
            await _reply(
                interaction,
                "Для Telegram укажите user_id или @username параметром user_id "
                "(упоминание Discord-пользователя сюда не подходит).",
            )
            return
        if not user_id:
            await _reply(
                interaction,
                "Укажите user (упоминание) или user_id / @username.",
            )
            return
        if platform.value == "telegram" and not parse_user_id(user_id):
            await _reply(
                interaction,
                "Telegram-админа по @тегу добавляйте в ЛС бота: "
                f"/addadmin {user_id if user_id.startswith('@') else '@' + user_id.lstrip('@')}",
            )
            return
        if platform.value == "discord" and not parse_user_id(user_id):
            resolved = await _resolve_discord_tag(interaction, user_id)
            if resolved is None:
                await _reply(
                    interaction,
                    format_bad_user_ref(user_id),
                )
                return
            await ui.admin_add(interaction, platform.value, resolved)
            return
        await ui.admin_add(interaction, platform.value, user_id)

    @app_commands.command(
        name="admin_remove", description="Снять права админа"
    )
    @app_commands.describe(
        platform="Платформа аккаунта",
        user="Упомянуть пользователя Discord",
        user_id="Или числовой id / @username",
    )
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def admin_remove(
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        user: discord.User | None = None,
        user_id: str | None = None,
    ) -> None:
        if user is not None and platform.value == "discord":
            await ui.admin_remove(interaction, platform.value, str(user.id))
            return
        if not user_id:
            await _reply(
                interaction,
                "Укажите user (упоминание) или user_id / @username.",
            )
            return
        if platform.value == "discord" and not parse_user_id(user_id):
            resolved = await _resolve_discord_tag(interaction, user_id)
            if resolved is None:
                await _reply(interaction, format_bad_user_ref(user_id))
                return
            await ui.admin_remove(interaction, platform.value, resolved)
            return
        await ui.admin_remove(interaction, platform.value, user_id)

    @app_commands.command(
        name="block_user", description="Заблокировать пользователя"
    )
    @app_commands.describe(
        platform="Платформа аккаунта",
        user_id="Числовой id пользователя",
        reason="Причина (необязательно)",
    )
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def block_user(
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        user_id: str,
        reason: str | None = None,
    ) -> None:
        await ui.block_user(interaction, platform.value, user_id, reason)

    @app_commands.command(
        name="unblock_user", description="Разблокировать пользователя"
    )
    @app_commands.describe(
        platform="Платформа аккаунта", user_id="Числовой id пользователя"
    )
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def unblock_user(
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        user_id: str,
    ) -> None:
        await ui.unblock_user(interaction, platform.value, user_id)

    @app_commands.command(
        name="blocked_list", description="Чёрный список (TG + DS)"
    )
    async def blocked_list(interaction: discord.Interaction) -> None:
        await ui.blocked_list(interaction)

    @app_commands.command(
        name="roles_propose",
        description="Роли, которые могут предлагать (пусто = все)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def roles_propose(interaction: discord.Interaction) -> None:
        await ui.roles_propose(interaction)

    @app_commands.command(
        name="roles_mod", description="Роли с доступом к модерации"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def roles_mod(interaction: discord.Interaction) -> None:
        await ui.roles_mod(interaction)

    @app_commands.command(
        name="admin_roles",
        description="Роли с правами админа бота (только владелец сервера)",
    )
    @app_commands.guild_only()
    async def admin_roles(interaction: discord.Interaction) -> None:
        await ui.admin_roles(interaction)

    @app_commands.command(
        name=app_commands.locale_str("bot_news", ru="новости_бота"),
        description="Краткий пост в #изменения-бота",
    )
    @app_commands.describe(text="3–8 строк для людей на сервере (+ ссылка на wiki)")
    @app_commands.rename(text="текст")
    async def bot_news(
        interaction: discord.Interaction, text: str
    ) -> None:
        await ui.bot_news(interaction, text)

    @app_commands.command(
        name="antiflood_config",
        description="Антифлуд: показать или изменить глобальные значения",
    )
    @app_commands.describe(
        limit="Сколько заявок на пользователя за окно",
        window_sec="Длина окна в секундах",
    )
    async def antiflood_config(
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 1000] | None = None,
        window_sec: app_commands.Range[int, 1, 86400] | None = None,
    ) -> None:
        await ui.antiflood_config(interaction, limit, window_sec)

    @app_commands.command(
        name="ratelimit_config",
        description="Лимит заявок этого сервера: показать или изменить",
    )
    @app_commands.describe(
        enabled="Включить лимит сервера",
        count="Сколько заявок за окно",
        window_sec="Длина окна в секундах",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def ratelimit_config(
        interaction: discord.Interaction,
        enabled: bool | None = None,
        count: app_commands.Range[int, 1, 1000] | None = None,
        window_sec: app_commands.Range[int, 1, 86400] | None = None,
    ) -> None:
        await ui.ratelimit_config(interaction, enabled, count, window_sec)

    commands = [
        admins_list,
        queue,
        download_bot,
        admin_add,
        admin_remove,
        block_user,
        unblock_user,
        blocked_list,
        roles_propose,
        roles_mod,
        admin_roles,
        bot_news,
        antiflood_config,
        ratelimit_config,
    ]
    tree = getattr(bot, "tree", None)
    if tree is None:
        raise TypeError(
            "Ожидается discord.ext.commands.Bot с атрибутом tree "
            "(или CommandTree в bot.tree)"
        )
    for command in commands:
        tree.add_command(command, override=True)
    return commands
