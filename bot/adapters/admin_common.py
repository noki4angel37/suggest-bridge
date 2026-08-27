"""Platform-agnostic RU texts for admin commands (Telegram + Discord).

Pure formatting and parsing only: no aiogram / discord.py imports, so both
adapters and the tests share the same wording.

Bootstrap: the very first owner is never added by a command — put the Telegram
user id into `ADMIN_IDS` and let `AdminService.bootstrap_telegram_admins()` seed
it on startup. Everyone else is added with `/addadmin` (TG) or `/admin_add`
(Discord) by an existing admin.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bot.core.models import Admin, BlacklistEntry, GuildConfig, Platform
from bot.core.services import (
    AdminService,
    AntifloodService,
    BlacklistService,
    GuildConfigService,
)

PLATFORM_LABELS: dict[Platform, str] = {
    Platform.telegram: "Telegram",
    Platform.discord: "Discord",
}

_PLATFORM_ALIASES: dict[str, Platform] = {
    "tg": Platform.telegram,
    "telegram": Platform.telegram,
    "тг": Platform.telegram,
    "телеграм": Platform.telegram,
    "телеграмм": Platform.telegram,
    "ds": Platform.discord,
    "dc": Platform.discord,
    "discord": Platform.discord,
    "дс": Platform.discord,
    "дискорд": Platform.discord,
}

BOOTSTRAP_NOTE = (
    "Первый владелец Telegram — ADMIN_IDS и/или OWNER_TELEGRAM_ID "
    "(bootstrap при старте). Discord-владелец — OWNER_DISCORD_ID "
    "(тоже bootstrap) или /adddiscordadmin из Telegram. "
    "Дальше админов добавляют командами."
)

DOWNLOAD_INSTRUCTIONS = "\n".join(
    (
        "Пакет suggest-bot для нового ПК (репозиторий не нужен).",
        "",
        "1) Скачайте suggest-bot.zip из следующего сообщения",
        "2) Распакуйте в любую папку, например %USERPROFILE%\\suggest-bot",
        "3) PowerShell:",
        "   cd %USERPROFILE%\\suggest-bot",
        "   .\\install-agent.ps1",
        "4) Скопируйте .env.example → .env и заполните:",
        "   BOT_TOKEN, DISCORD_TOKEN, ADMIN_IDS, CHANNEL_ID, OWNER_*, HOST_SYNC_SECRET",
        "   (алиас local.env по-прежнему читается)",
        "5) Syncthing: folder id suggest-host-sync →",
        "   %LOCALAPPDATA%\\suggest-host-sync (шарьте с другими админ-ПК)",
        "6) .\\run-agent.ps1",
        "7) В боте: /host — должен появиться ваш ПК",
        "",
        "В архиве: код, install-agent.ps1, run-agent.ps1, SETUP.md,",
        "requirements.txt, .env.example.",
        "Секреты (.env) в архив не входят — возьмите у владельца.",
        "",
        "Нужны: Windows 10/11, Python 3.11+ в PATH, Syncthing.",
    )
)

TELEGRAM_ADMIN_HELP = "\n".join(
    (
        "Админ-команды (только в личке бота):",
        "",
        "Админы:",
        "/admins — список админов обеих платформ",
        "/addadmin <tg_id|@username> — выдать права в Telegram",
        "/deladmin <tg_id|@username> — снять права в Telegram",
        "/adddiscordadmin <discord_id|@user> — выдать права в Discord",
        "/deldiscordadmin <discord_id> — снять права в Discord",
        "",
        "Чёрный список:",
        "/blocked — кто заблокирован",
        "/block <платформа> <id> [причина] — заблокировать",
        "/unblock <платформа> <id> — разблокировать",
        "Платформа: tg или ds.",
        "",
        "Антифлуд и лимиты:",
        "/antiflood — текущие глобальные значения",
        "/antiflood set <лимит> <секунды> — изменить значения",
        "/ratelimit — как работает лимит заявок на сервере Discord",
        "",
        "Очередь:",
        "/queue — заявки на модерации и отложенные публикации",
        "",
        "Хост (multi-PC primary):",
        "/host — панель: кто primary, запросы передачи, force (супер-админ)",
        "/host_consent — разрешить этому ПК быть primary (лизинг getUpdates)",
        "/host_release — снять согласие и отдать primary (failover)",
        "/host_status — кто держит лизинг сейчас",
        "/download — инструкция + zip пакета для нового ПК",
        "",
        "Супер-админ (OWNER_TELEGRAM_ID / OWNER_DISCORD_ID): force-передача,",
        "удалённый start/stop на ПК с агентом.",
        "",
        "Зеркало TG↔Discord:",
        "/mirror on|off|status — включить/выключить синхронизацию ленты",
        "/repost <tg_message_id> — вручную перепостить пост канала в Discord",
        "",
        "/adminhelp — эта справка",
        "",
        BOOTSTRAP_NOTE,
    )
)

RATE_LIMIT_EXPLAINER = "\n".join(
    (
        "Лимит заявок (rate limit) — это отдельная от антифлуда настройка "
        "конкретного сервера Discord.",
        "",
        "• Антифлуд работает всегда и для всех: /antiflood задаёт "
        "глобальные лимит и окно.",
        "• Лимит сервера по умолчанию выключен. Если его включить, для "
        "участников этого сервера действуют его числа вместо глобальных.",
        "• Настраивается из Discord командой /ratelimit_config "
        "(нужен id сервера), потому что значения хранятся в настройках "
        "сервера.",
        "",
        "Из Telegram лимит сервера не меняется — здесь только просмотр "
        "глобальных значений антифлуда.",
    )
)


@dataclass(frozen=True)
class AdminServices:
    """Services an admin UI needs; adapters get it from the app container."""

    admins: AdminService
    blacklist: BlacklistService
    antiflood: AntifloodService
    guilds: GuildConfigService


_SERVICE_ALIASES: dict[str, tuple[str, ...]] = {
    "admins": ("admins", "admin", "admin_service", "admins_service"),
    "blacklist": ("blacklist", "blacklist_service", "blacklists"),
    "antiflood": ("antiflood", "antiflood_service"),
    "guilds": (
        "guilds",
        "guild_config",
        "guild_configs",
        "guild_config_service",
    ),
}


def resolve_admin_services(services: Any) -> AdminServices:
    """Accept an `AdminServices`, a mapping or any container-like object."""
    if isinstance(services, AdminServices):
        return services

    def pick(field: str) -> Any:
        for name in _SERVICE_ALIASES[field]:
            value = (
                services.get(name)
                if isinstance(services, Mapping)
                else getattr(services, name, None)
            )
            if value is not None:
                return value
        return None

    found = {field: pick(field) for field in _SERVICE_ALIASES}
    missing = sorted(name for name, value in found.items() if value is None)
    if missing:
        raise TypeError(
            "Не найдены сервисы для админ-команд: " + ", ".join(missing)
        )
    return AdminServices(**found)


def parse_platform(raw: str | None) -> Platform | None:
    if not raw:
        return None
    return _PLATFORM_ALIASES.get(raw.strip().lower().lstrip("/"))


def platform_label(platform: Platform) -> str:
    return PLATFORM_LABELS.get(Platform(platform), str(platform))


def parse_user_id(raw: str | None) -> str | None:
    """Normalize a numeric user id: accepts `123`, `@123`, `<@123>`, `<@!123>`."""
    if raw is None:
        return None
    cleaned = raw.strip().strip("<>").lstrip("@").lstrip("!")
    return cleaned if cleaned.isdigit() else None


def parse_username_tag(raw: str | None) -> str | None:
    """Extract @username without @; None if empty or purely numeric (that is an id)."""
    if raw is None:
        return None
    cleaned = raw.strip().strip("<>").lstrip("@").lstrip("!")
    if not cleaned or cleaned.isdigit():
        return None
    # Telegram usernames: 5–32 chars, letters/digits/underscore.
    if cleaned.startswith("http"):
        return None
    return cleaned


def format_bad_user_ref(raw: str) -> str:
    return (
        f"Не понял пользователя «{raw}». "
        "Укажите числовой id, @username или упомяните человека."
    )


def plural_submissions(count: int) -> str:
    tail, hundred = count % 10, count % 100
    if tail == 1 and hundred != 11:
        return "заявка"
    if 2 <= tail <= 4 and not 12 <= hundred <= 14:
        return "заявки"
    return "заявок"


def format_queue_report(
    pending: Sequence[Any],
    scheduled: Sequence[Any],
    *,
    now: datetime | None = None,
) -> str:
    """Human-readable moderation queue for /queue (TG and Discord)."""
    from bot.core.models import Source, utcnow

    moment = now or utcnow()
    lines: list[str] = []

    lines.append(f"⏳ На модерации ({len(pending)}):")
    if not pending:
        lines.append("  (пусто)")
    else:
        for item in pending:
            preview = (getattr(item, "text", None) or "").strip().replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:57] + "…"
            if not preview:
                preview = "без текста"
            source = getattr(item, "source", None)
            src = (
                "TG"
                if source is Source.telegram
                else "DS"
                if source is Source.discord
                else "?"
            )
            lines.append(f"  #{item.id} [{src}] {preview}")

    lines.append("")
    lines.append(f"🕓 Отложенные ({len(scheduled)}):")
    if not scheduled:
        lines.append("  (пусто)")
    else:
        for item in scheduled:
            when = getattr(item, "scheduled_at", None)
            if when is None:
                stamp = "?"
                overdue = False
            else:
                local = when.astimezone() if when.tzinfo else when
                stamp = local.strftime("%d.%m.%Y %H:%M")
                overdue = when <= moment
            flag = " ⚠ просрочено" if overdue else ""
            preview = (getattr(item, "text", None) or "").strip().replace("\n", " ")
            if len(preview) > 40:
                preview = preview[:37] + "…"
            if not preview:
                preview = "без текста"
            lines.append(f"  #{item.id} до {stamp}{flag} — {preview}")

    return "\n".join(lines)


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d.%m.%Y")


def _group_by_platform(items: Sequence[Any]) -> dict[Platform, list[Any]]:
    grouped: dict[Platform, list[Any]] = {}
    for item in items:
        grouped.setdefault(Platform(item.platform), []).append(item)
    return grouped


def format_admins_list(admins: Sequence[Admin]) -> str:
    if not admins:
        return "Админов нет — база пустая.\n\n" + BOOTSTRAP_NOTE

    grouped = _group_by_platform(admins)
    lines = [f"Админы ({len(admins)}):"]
    for platform in (Platform.telegram, Platform.discord):
        entries = grouped.get(platform)
        if not entries:
            continue
        lines.append("")
        lines.append(f"{platform_label(platform)}:")
        for admin in entries:
            details = []
            if admin.added_by:
                details.append(f"добавил: {admin.added_by}")
            added_at = _format_dt(admin.created_at)
            if added_at:
                details.append(added_at)
            suffix = f" — {', '.join(details)}" if details else ""
            lines.append(f"• {admin.platform_user_id}{suffix}")
    return "\n".join(lines)


def format_blocked_list(entries: Sequence[BlacklistEntry]) -> str:
    if not entries:
        return "Чёрный список пуст."

    grouped = _group_by_platform(entries)
    lines = [f"Чёрный список ({len(entries)}):"]
    for platform in (Platform.telegram, Platform.discord):
        blocked = grouped.get(platform)
        if not blocked:
            continue
        lines.append("")
        lines.append(f"{platform_label(platform)}:")
        for entry in blocked:
            reason = (entry.reason or "").strip()
            tail = f"причина: {reason}" if reason else "без причины"
            blocked_at = _format_dt(entry.created_at)
            if blocked_at:
                tail = f"{tail}, {blocked_at}"
            lines.append(f"• {entry.platform_user_id} — {tail}")
    return "\n".join(lines)


def _role_lines(
    role_ids: Sequence[str], names: Mapping[str, str] | None
) -> list[str]:
    lines = []
    for role_id in role_ids:
        name = (names or {}).get(str(role_id))
        lines.append(f"• {name} ({role_id})" if name else f"• {role_id}")
    return lines


def format_roles_config(
    config: GuildConfig,
    *,
    propose_names: Mapping[str, str] | None = None,
    mod_names: Mapping[str, str] | None = None,
) -> str:
    lines = [f"Роли сервера {config.guild_id}:", "", "Могут предлагать:"]
    if config.propose_role_ids:
        lines.extend(_role_lines(config.propose_role_ids, propose_names))
    else:
        lines.append("• все участники (роли не заданы)")
    lines.extend(("", "Могут модерировать:"))
    if config.mod_role_ids:
        lines.extend(_role_lines(config.mod_role_ids, mod_names))
    else:
        lines.append("• только админы бота (роли не заданы)")
    return "\n".join(lines)


def format_antiflood_status(
    limit: int, window_sec: int, *, hint: str | None = None
) -> str:
    lines = [
        "Антифлуд (глобальные значения, работает всегда):",
        f"• лимит: {limit} {plural_submissions(limit)}",
        f"• окно: {window_sec} сек",
    ]
    if hint:
        lines.extend(("", hint))
    return "\n".join(lines)


def format_rate_limit_status(
    config: GuildConfig, *, hint: str | None = None
) -> str:
    if not config.rate_limit_enabled:
        lines = [
            f"Лимит заявок сервера {config.guild_id}: выключен.",
            "Действуют глобальные значения антифлуда.",
        ]
    else:
        count = config.rate_limit_count
        window = config.rate_limit_window_sec
        lines = [
            f"Лимит заявок сервера {config.guild_id}: включён.",
            f"• заявок: {count}"
            if count is not None
            else "• заявок: не задано (берётся из антифлуда)",
            f"• окно: {window} сек"
            if window is not None
            else "• окно: не задано (берётся из антифлуда)",
        ]
    if hint:
        lines.extend(("", hint))
    return "\n".join(lines)


def format_admin_added(admin: Admin, *, was_new: bool = True) -> str:
    label = platform_label(admin.platform)
    if not was_new:
        return f"{label} {admin.platform_user_id} уже был админом."
    return f"Добавлен админ {label} {admin.platform_user_id}."


def format_admin_removed(platform: Platform, user_id: str) -> str:
    return f"Права админа сняты: {platform_label(platform)} {user_id}."


def format_admin_not_found(platform: Platform, user_id: str) -> str:
    return f"{platform_label(platform)} {user_id} не был админом."


def format_last_admin_guard(platform: Platform) -> str:
    return (
        f"Нельзя снять права последнего админа {platform_label(platform)} — "
        "иначе бот останется без владельца.\n\n" + BOOTSTRAP_NOTE
    )


def format_self_removal_guard() -> str:
    return "Свои права админа снять нельзя — попросите другого админа."


def format_blocked_added(entry: BlacklistEntry) -> str:
    label = platform_label(entry.platform)
    reason = (entry.reason or "").strip()
    tail = f" Причина: {reason}" if reason else ""
    return f"Заблокирован {label} {entry.platform_user_id}.{tail}"


def format_unblocked(platform: Platform, user_id: str) -> str:
    return f"Разблокирован {platform_label(platform)} {user_id}."


def format_not_blocked(platform: Platform, user_id: str) -> str:
    return f"{platform_label(platform)} {user_id} не был заблокирован."


def format_block_admin_guard(platform: Platform, user_id: str) -> str:
    return (
        f"{platform_label(platform)} {user_id} — админ. "
        "Сначала снимите права, потом блокируйте."
    )


def format_bad_platform(raw: str | None) -> str:
    shown = (raw or "").strip() or "—"
    return f"Не понял платформу: {shown}. Ожидаю tg или ds."


def format_bad_user_id(raw: str | None) -> str:
    shown = (raw or "").strip() or "—"
    return f"Не понял id пользователя: {shown}. Ожидаю число."


def format_bad_numbers() -> str:
    return "Лимит и окно должны быть целыми числами больше нуля."


def format_usage(usage: str) -> str:
    return f"Формат: {usage}"


def format_antiflood_updated(limit: int, window_sec: int) -> str:
    return (
        "Антифлуд обновлён: "
        f"{limit} {plural_submissions(limit)} за {window_sec} сек."
    )


def format_roles_updated(
    kind: str, role_ids: Sequence[str], *, names: Mapping[str, str] | None = None
) -> str:
    """`kind` is a RU genitive phrase, e.g. "предлагать" / "модерировать"."""
    if not role_ids:
        default = (
            "теперь могут все участники"
            if kind == "предлагать"
            else "теперь могут только админы бота"
        )
        return f"Роли сброшены: {kind} — {default}."
    listed = "\n".join(_role_lines(role_ids, names))
    return f"Роли обновлены — могут {kind}:\n{listed}"


def format_rate_limit_updated(config: GuildConfig) -> str:
    return "Настройка сохранена.\n\n" + format_rate_limit_status(config)
