"""Telegram admin commands: admins, blacklist, antiflood, rate limit help.

All commands live in the bot's private chat and are available only to users
listed as Telegram admins in the bridge database. The first owner comes from
`ADMIN_IDS` via `AdminService.bootstrap_telegram_admins()`; there is no way to
create an admin from scratch by a command.

Wiring (see `register_telegram_settings`):

    from bot.adapters.telegram.handlers_settings import register_telegram_settings
    register_telegram_settings(dp, services)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import FSInputFile, Message

from bot.adapters.admin_common import (
    DOWNLOAD_INSTRUCTIONS,
    RATE_LIMIT_EXPLAINER,
    TELEGRAM_ADMIN_HELP,
    AdminServices,
    format_admin_added,
    format_admin_not_found,
    format_admin_removed,
    format_admins_list,
    format_antiflood_status,
    format_antiflood_updated,
    format_bad_numbers,
    format_bad_platform,
    format_bad_user_ref,
    format_block_admin_guard,
    format_blocked_added,
    format_blocked_list,
    format_last_admin_guard,
    format_not_blocked,
    format_queue_report,
    format_self_removal_guard,
    format_unblocked,
    format_usage,
    parse_platform,
    parse_user_id,
    parse_username_tag,
    resolve_admin_services,
)
from bot.core.host_lease import (
    HostLeaseError,
    grant_consent,
    resolve_host_id,
    revoke_consent,
    status as host_lease_status,
)
from bot.core.models import Platform
from bot.core.pack_dist import build_suggest_bot_zip

logger = logging.getLogger(__name__)

ANTIFLOOD_HINT = "Изменить: /antiflood set <лимит> <секунды>"
MAX_ANTIFLOOD_LIMIT = 1000
MAX_ANTIFLOOD_WINDOW_SEC = 86_400
DOWNLOAD_CAPTION = (
    "Распакуйте zip → .\\install-agent.ps1 → local.env → "
    "Syncthing suggest-host-sync → .\\run-agent.ps1 → /host"
)


class TelegramAdminFilter(BaseFilter):
    """Passes only Telegram admins from the database (list is dynamic)."""

    def __init__(self, services: AdminServices) -> None:
        self.services = services

    async def __call__(self, message: Message) -> bool:
        user = message.from_user
        if user is None:
            return False
        return self.services.admins.can_manage(Platform.telegram, str(user.id))


class TelegramSettingsUI:
    """Command handlers; every method answers in Russian, plain text."""

    def __init__(
        self, services: AdminServices, *, container: Any | None = None
    ) -> None:
        self.services = services
        self.container = container

    # --- admins --------------------------------------------------------------

    async def cmd_help(self, message: Message) -> None:
        await message.answer(TELEGRAM_ADMIN_HELP)

    async def cmd_queue(self, message: Message) -> None:
        submissions = getattr(self.container, "submissions", None)
        if submissions is None:
            await message.answer("Сервис заявок недоступен в этом процессе.")
            return
        pending = submissions.list_pending(limit=30)
        scheduled = submissions.list_scheduled(limit=30)
        await message.answer(format_queue_report(pending, scheduled))

    async def cmd_download(self, message: Message) -> None:
        """Send install instructions + standalone zip (no secrets)."""
        await message.answer(DOWNLOAD_INSTRUCTIONS)
        status = await message.answer("Собираю пакет…")
        try:
            zip_path = await asyncio.to_thread(build_suggest_bot_zip)
        except Exception:  # noqa: BLE001
            logger.exception("pack suggest-bot zip failed")
            await status.edit_text(
                "Не удалось собрать пакет. Проверьте логи primary-ПК."
            )
            return
        try:
            await message.answer_document(
                FSInputFile(str(zip_path), filename="suggest-bot.zip"),
                caption=DOWNLOAD_CAPTION,
            )
            await status.edit_text("Готово — zip в сообщении выше.")
        except Exception:  # noqa: BLE001
            logger.exception("send suggest-bot zip failed")
            await status.edit_text(
                "Пакет собран, но отправить файл не удалось "
                f"({zip_path.name})."
            )

    async def cmd_admins(self, message: Message) -> None:
        await message.answer(
            format_admins_list(self.services.admins.list_admins())
        )

    async def cmd_add_admin(
        self, message: Message, command: CommandObject
    ) -> None:
        await self._add_admin(message, command, Platform.telegram)

    async def cmd_del_admin(
        self, message: Message, command: CommandObject
    ) -> None:
        await self._remove_admin(message, command, Platform.telegram)

    async def cmd_add_discord_admin(
        self, message: Message, command: CommandObject
    ) -> None:
        await self._add_admin(message, command, Platform.discord)

    async def cmd_del_discord_admin(
        self, message: Message, command: CommandObject
    ) -> None:
        await self._remove_admin(message, command, Platform.discord)

    async def _add_admin(
        self, message: Message, command: CommandObject, platform: Platform
    ) -> None:
        raw = (command.args or "").split()
        if len(raw) != 1:
            await message.answer(
                format_usage(f"/{command.command} <@username|id>")
            )
            return
        user_id = await self._resolve_user_ref(message, raw[0], platform)
        if user_id is None:
            return

        try:
            was_new = not self.services.admins.is_admin(platform, user_id)
            admin = await self.services.admins.add_admin(
                platform, user_id, added_by=self._actor(message)
            )
            await message.answer(format_admin_added(admin, was_new=was_new))
        except Exception:  # noqa: BLE001
            logger.exception("add_admin failed platform=%s", platform.value)
            await message.answer(
                "Не удалось добавить админа. Подробности в data/bot-run.log."
            )

    async def _remove_admin(
        self, message: Message, command: CommandObject, platform: Platform
    ) -> None:
        raw = (command.args or "").split()
        if len(raw) != 1:
            await message.answer(
                format_usage(f"/{command.command} <@username|id>")
            )
            return
        user_id = await self._resolve_user_ref(message, raw[0], platform)
        if user_id is None:
            return

        actor = message.from_user
        if (
            platform is Platform.telegram
            and actor is not None
            and str(actor.id) == user_id
        ):
            await message.answer(format_self_removal_guard())
            return
        if self._is_last_telegram_admin(platform, user_id):
            await message.answer(format_last_admin_guard(platform))
            return

        removed = await self.services.admins.remove_admin(platform, user_id)
        await message.answer(
            format_admin_removed(platform, user_id)
            if removed
            else format_admin_not_found(platform, user_id)
        )

    async def _resolve_user_ref(
        self, message: Message, raw: str, platform: Platform
    ) -> str | None:
        """Resolve numeric id, @username, or text/ mention entity to platform id."""
        # Prefer mention entities from the command message (tap-to-mention).
        if message.entities and message.text:
            for entity in message.entities:
                if entity.type == "text_mention" and entity.user is not None:
                    if platform is Platform.telegram:
                        return str(entity.user.id)
                if entity.type == "mention":
                    tag = message.text[entity.offset : entity.offset + entity.length]
                    resolved = await self._resolve_telegram_username(tag)
                    if resolved is not None and platform is Platform.telegram:
                        return resolved

        user_id = parse_user_id(raw)
        if user_id is not None:
            return user_id

        tag = parse_username_tag(raw)
        if tag is None:
            await message.answer(format_bad_user_ref(raw))
            return None

        if platform is Platform.telegram:
            resolved = await self._resolve_telegram_username(tag)
            if resolved is None:
                await message.answer(
                    f"Не нашёл Telegram-пользователя @{tag}. "
                    "Человек должен хотя бы раз написать боту, либо укажите числовой id."
                )
                return None
            return resolved

        # Discord from TG: numeric id only (can't resolve Discord tags via TG API).
        await message.answer(
            "Для Discord укажите числовой id или добавьте админа командой "
            "/admin_add в Discord (там можно упомянуть @тег)."
        )
        return None

    async def _resolve_telegram_username(self, tag: str) -> str | None:
        bot = getattr(self.container, "bot", None)
        if bot is None:
            return None
        username = tag.lstrip("@")
        try:
            chat = await bot.get_chat(f"@{username}")
        except Exception:  # noqa: BLE001
            return None
        chat_id = getattr(chat, "id", None)
        return str(chat_id) if chat_id is not None else None

    def _is_last_telegram_admin(
        self, platform: Platform, user_id: str
    ) -> bool:
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

    async def cmd_blocked(self, message: Message) -> None:
        await message.answer(
            format_blocked_list(self.services.blacklist.list_blocked())
        )

    async def cmd_block(
        self, message: Message, command: CommandObject
    ) -> None:
        parts = (command.args or "").split(maxsplit=2)
        if len(parts) < 2:
            await message.answer(
                format_usage("/block <платформа> <id> [причина]")
            )
            return
        platform = parse_platform(parts[0])
        if platform is None:
            await message.answer(format_bad_platform(parts[0]))
            return
        user_id = parse_user_id(parts[1])
        if user_id is None:
            await message.answer(format_bad_user_id(parts[1]))
            return
        if self.services.admins.is_admin(platform, user_id):
            await message.answer(format_block_admin_guard(platform, user_id))
            return

        reason = parts[2].strip() if len(parts) > 2 else None
        entry = await self.services.blacklist.block(
            platform, user_id, reason=reason or None
        )
        await message.answer(format_blocked_added(entry))

    async def cmd_unblock(
        self, message: Message, command: CommandObject
    ) -> None:
        parts = (command.args or "").split()
        if len(parts) != 2:
            await message.answer(format_usage("/unblock <платформа> <id>"))
            return
        platform = parse_platform(parts[0])
        if platform is None:
            await message.answer(format_bad_platform(parts[0]))
            return
        user_id = parse_user_id(parts[1])
        if user_id is None:
            await message.answer(format_bad_user_id(parts[1]))
            return

        removed = await self.services.blacklist.unblock(platform, user_id)
        await message.answer(
            format_unblocked(platform, user_id)
            if removed
            else format_not_blocked(platform, user_id)
        )

    # --- antiflood / rate limit ---------------------------------------------

    async def cmd_antiflood(
        self, message: Message, command: CommandObject
    ) -> None:
        antiflood = self.services.antiflood
        parts = (command.args or "").split()
        if not parts:
            await message.answer(
                format_antiflood_status(
                    antiflood.limit, antiflood.window_sec, hint=ANTIFLOOD_HINT
                )
            )
            return
        if parts[0].lower() != "set" or len(parts) != 3:
            await message.answer(
                format_usage("/antiflood set <лимит> <секунды>")
            )
            return

        numbers = _parse_positive_ints(parts[1], parts[2])
        if numbers is None:
            await message.answer(format_bad_numbers())
            return
        limit, window_sec = numbers
        if limit > MAX_ANTIFLOOD_LIMIT or window_sec > MAX_ANTIFLOOD_WINDOW_SEC:
            await message.answer(
                "Слишком большие значения: лимит до "
                f"{MAX_ANTIFLOOD_LIMIT}, окно до "
                f"{MAX_ANTIFLOOD_WINDOW_SEC} сек."
            )
            return

        antiflood.configure_defaults(limit=limit, window_sec=window_sec)
        await message.answer(format_antiflood_updated(limit, window_sec))

    async def cmd_ratelimit(self, message: Message) -> None:
        await message.answer(RATE_LIMIT_EXPLAINER)

    # --- multi-PC host lease -------------------------------------------------

    async def cmd_host_status(self, message: Message) -> None:
        db = self.services.admins.db
        info = host_lease_status(db)
        lines = [
            "Статус lease (без имён ПК):",
            f"лизинг активен: {'да' if info.lease_active else 'нет'}",
            f"мы primary: {'да' if info.is_primary else 'нет'}",
            f"lease_until: {info.lease_until or '—'}",
            f"heartbeat: {info.heartbeat_at or '—'}",
            "Согласие: "
            + ("есть" if info.consent_host or info.consent_admin else "нет"),
        ]
        await message.answer("\n".join(lines))

    async def cmd_host_consent(self, message: Message) -> None:
        db = self.services.admins.db
        host_id = resolve_host_id()
        admin_id = self._actor(message).removeprefix("tg:")
        try:
            info = grant_consent(db, host_id, admin_id=admin_id)
        except HostLeaseError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            "Согласие на primary записано.\n"
            f"lease_until={info.lease_until}\n"
            "Этот ПК может держать getUpdates; другие с тем же bridge.db "
            "не стартуют, пока лизинг жив или согласие не снято (/host_release)."
        )

    async def cmd_host_release(self, message: Message) -> None:
        db = self.services.admins.db
        host_id = resolve_host_id()
        revoke_consent(db, host_id)
        await message.answer(
            "Согласие снято, лизинг primary очищен.\n"
            "Можно остановить этот процесс и поднять бота на другом ПК через /host."
        )

    # --- channel mirror ------------------------------------------------------

    async def cmd_mirror(
        self, message: Message, command: CommandObject
    ) -> None:
        mirror = getattr(self.container, "mirror", None)
        db = self.services.admins.db
        from bot.core.publish_router import is_mirror_enabled, set_mirror_enabled

        arg = (command.args or "").strip().lower()
        if arg in {"on", "1", "вкл", "enable"}:
            was = is_mirror_enabled(db, default=True)
            set_mirror_enabled(db, True)
            if mirror is not None and not was:
                await mirror.post_info_announcement()
            await message.answer("Зеркало TG↔Discord включено.")
            return
        if arg in {"off", "0", "выкл", "disable"}:
            set_mirror_enabled(db, False)
            await message.answer("Зеркало TG↔Discord выключено.")
            return
        state = "вкл" if is_mirror_enabled(db, default=True) else "выкл"
        await message.answer(
            f"Зеркало сейчас: {state}.\n"
            "Команды: /mirror on | /mirror off"
        )

    async def cmd_repost(
        self, message: Message, command: CommandObject
    ) -> None:
        mirror = getattr(self.container, "mirror", None)
        if mirror is None:
            await message.answer("Зеркало не подключено в этом процессе.")
            return
        raw = (command.args or "").strip()
        if not raw.isdigit():
            await message.answer(
                "Использование: /repost <tg_message_id>\n"
                "Перепостит сообщение из TG-канала в Discord-ленту."
            )
            return
        ok = await mirror.repost_from_telegram(int(raw))
        await message.answer(
            "Перепост отправлен." if ok else "Не удалось перепостить."
        )

    @staticmethod
    def _actor(message: Message) -> str:
        user = message.from_user
        return f"tg:{user.id}" if user is not None else "tg:unknown"


def _parse_positive_ints(*values: str) -> tuple[int, ...] | None:
    parsed = []
    for value in values:
        if not value.isdigit():
            return None
        number = int(value)
        if number <= 0:
            return None
        parsed.append(number)
    return tuple(parsed)


def register_telegram_settings(router: Any, services: Any) -> Router:
    """Attach admin commands to `router` (a Dispatcher or a parent Router).

    Handlers live in an own sub-router filtered to private chats and Telegram
    admins, so non-admin updates fall through to the user handlers untouched.
    """
    resolved = resolve_admin_services(services)
    ui = TelegramSettingsUI(resolved, container=services)

    settings = Router(name="admin_settings")
    settings.message.filter(
        F.chat.type == ChatType.PRIVATE, TelegramAdminFilter(resolved)
    )

    commands: tuple[tuple[tuple[str, ...], Any], ...] = (
        (("adminhelp", "adminsettings"), ui.cmd_help),
        (("queue",), ui.cmd_queue),
        (("download", "getbot", "install_pc"), ui.cmd_download),
        (("admins",), ui.cmd_admins),
        (("addadmin",), ui.cmd_add_admin),
        (("deladmin",), ui.cmd_del_admin),
        (("adddiscordadmin",), ui.cmd_add_discord_admin),
        (("deldiscordadmin",), ui.cmd_del_discord_admin),
        (("blocked",), ui.cmd_blocked),
        (("block",), ui.cmd_block),
        (("unblock",), ui.cmd_unblock),
        (("antiflood",), ui.cmd_antiflood),
        (("ratelimit",), ui.cmd_ratelimit),
        (("host_consent",), ui.cmd_host_consent),
        (("host_release",), ui.cmd_host_release),
        (("host_status",), ui.cmd_host_status),
        (("mirror",), ui.cmd_mirror),
        (("repost",), ui.cmd_repost),
    )
    for names, handler in commands:
        settings.message.register(handler, Command(*names))

    router.include_router(settings)
    return settings
