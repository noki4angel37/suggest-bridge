from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.adapters.admin_common import (
    BOOTSTRAP_NOTE,
    DOWNLOAD_INSTRUCTIONS,
    TELEGRAM_ADMIN_HELP,
    AdminServices,
    format_admin_added,
    format_admin_not_found,
    format_admin_removed,
    format_admins_list,
    format_antiflood_status,
    format_antiflood_updated,
    format_blocked_added,
    format_blocked_list,
    format_last_admin_guard,
    format_rate_limit_status,
    format_roles_config,
    format_roles_updated,
    parse_platform,
    parse_user_id,
    platform_label,
    plural_submissions,
    resolve_admin_services,
)
from bot.core.models import Admin, BlacklistEntry, GuildConfig, Platform

CREATED_AT = datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("tg", Platform.telegram),
        ("TG", Platform.telegram),
        (" телеграм ", Platform.telegram),
        ("ds", Platform.discord),
        ("discord", Platform.discord),
        ("дс", Platform.discord),
        ("vk", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_platform(raw: str | None, expected: Platform | None) -> None:
    assert parse_platform(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123", "123"),
        (" 123 ", "123"),
        ("@123", "123"),
        ("<@123>", "123"),
        ("<@!123>", "123"),
        ("abc", None),
        ("12a", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_user_id(raw: str | None, expected: str | None) -> None:
    assert parse_user_id(raw) == expected


def test_platform_label() -> None:
    assert platform_label(Platform.telegram) == "Telegram"
    assert platform_label(Platform.discord) == "Discord"


def test_format_admins_list_empty_mentions_bootstrap() -> None:
    text = format_admins_list([])
    assert "Админов нет" in text
    assert BOOTSTRAP_NOTE in text
    assert "ADMIN_IDS" in text


def test_format_admins_list_groups_by_platform() -> None:
    admins = [
        Admin(
            platform=Platform.telegram,
            platform_user_id="111",
            added_by="bootstrap",
            created_at=CREATED_AT,
        ),
        Admin(platform=Platform.discord, platform_user_id="333"),
    ]
    text = format_admins_list(admins)
    lines = text.splitlines()
    assert lines[0] == "Админы (2):"
    assert "Telegram:" in lines
    assert "• 111 — добавил: bootstrap, 12.08.2026" in lines
    assert "Discord:" in lines
    assert "• 333" in lines
    # Telegram block always comes first.
    assert lines.index("Telegram:") < lines.index("Discord:")


def test_format_blocked_list_empty() -> None:
    assert format_blocked_list([]) == "Чёрный список пуст."


def test_format_blocked_list_with_and_without_reason() -> None:
    entries = [
        BlacklistEntry(
            platform=Platform.telegram,
            platform_user_id="555",
            reason="Спам",
            created_at=CREATED_AT,
        ),
        BlacklistEntry(platform=Platform.discord, platform_user_id="777"),
    ]
    text = format_blocked_list(entries)
    assert text.startswith("Чёрный список (2):")
    assert "• 555 — причина: Спам, 12.08.2026" in text
    assert "• 777 — без причины" in text


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "заявка"),
        (2, "заявки"),
        (4, "заявки"),
        (5, "заявок"),
        (11, "заявок"),
        (14, "заявок"),
        (21, "заявка"),
        (102, "заявки"),
    ],
)
def test_plural_submissions(count: int, expected: str) -> None:
    assert plural_submissions(count) == expected


def test_format_antiflood_status() -> None:
    text = format_antiflood_status(5, 60, hint="Изменить: /antiflood set")
    assert "• лимит: 5 заявок" in text
    assert "• окно: 60 сек" in text
    assert text.endswith("Изменить: /antiflood set")


def test_format_antiflood_status_without_hint() -> None:
    assert "Изменить" not in format_antiflood_status(3, 30)


def test_format_antiflood_updated() -> None:
    assert format_antiflood_updated(3, 30) == (
        "Антифлуд обновлён: 3 заявки за 30 сек."
    )
    assert format_antiflood_updated(1, 60) == (
        "Антифлуд обновлён: 1 заявка за 60 сек."
    )


def test_format_roles_config_defaults_are_explained() -> None:
    text = format_roles_config(GuildConfig(guild_id="900"))
    assert "Роли сервера 900:" in text
    assert "• все участники (роли не заданы)" in text
    assert "• только админы бота (роли не заданы)" in text


def test_format_roles_config_uses_role_names() -> None:
    config = GuildConfig(
        guild_id="900", propose_role_ids=["10", "11"], mod_role_ids=["20"]
    )
    text = format_roles_config(
        config,
        propose_names={"10": "Участник"},
        mod_names={"20": "Модератор"},
    )
    assert "• Участник (10)" in text
    assert "• 11" in text
    assert "• Модератор (20)" in text


def test_format_roles_updated_reset_messages() -> None:
    assert format_roles_updated("предлагать", []).endswith(
        "предлагать — теперь могут все участники."
    )
    assert format_roles_updated("модерировать", []).endswith(
        "модерировать — теперь могут только админы бота."
    )


def test_format_roles_updated_lists_roles() -> None:
    text = format_roles_updated(
        "модерировать", ["20"], names={"20": "Модератор"}
    )
    assert text.startswith("Роли обновлены — могут модерировать:")
    assert "• Модератор (20)" in text


def test_format_rate_limit_status_disabled() -> None:
    text = format_rate_limit_status(GuildConfig(guild_id="900"))
    assert "выключен" in text
    assert "глобальные значения антифлуда" in text


def test_format_rate_limit_status_enabled() -> None:
    config = GuildConfig(
        guild_id="900",
        rate_limit_enabled=True,
        rate_limit_count=3,
        rate_limit_window_sec=600,
    )
    text = format_rate_limit_status(config, hint="Изменить: /ratelimit_config")
    assert "включён" in text
    assert "• заявок: 3" in text
    assert "• окно: 600 сек" in text
    assert text.endswith("Изменить: /ratelimit_config")


def test_format_rate_limit_status_enabled_without_numbers() -> None:
    config = GuildConfig(guild_id="900", rate_limit_enabled=True)
    text = format_rate_limit_status(config)
    assert "• заявок: не задано (берётся из антифлуда)" in text
    assert "• окно: не задано (берётся из антифлуда)" in text


def test_format_admin_change_messages() -> None:
    admin = Admin(platform=Platform.discord, platform_user_id="333")
    assert format_admin_added(admin) == "Добавлен админ Discord 333."
    assert format_admin_added(admin, was_new=False) == (
        "Discord 333 уже был админом."
    )
    assert format_admin_removed(Platform.telegram, "111") == (
        "Права админа сняты: Telegram 111."
    )
    assert format_admin_not_found(Platform.telegram, "111") == (
        "Telegram 111 не был админом."
    )


def test_format_last_admin_guard_explains_bootstrap() -> None:
    text = format_last_admin_guard(Platform.telegram)
    assert "последнего админа Telegram" in text
    assert BOOTSTRAP_NOTE in text


def test_format_blocked_added_keeps_reason_optional() -> None:
    with_reason = format_blocked_added(
        BlacklistEntry(
            platform=Platform.telegram, platform_user_id="5", reason="Спам"
        )
    )
    assert with_reason == "Заблокирован Telegram 5. Причина: Спам"
    without_reason = format_blocked_added(
        BlacklistEntry(platform=Platform.telegram, platform_user_id="5")
    )
    assert without_reason == "Заблокирован Telegram 5."


def test_telegram_help_lists_every_command() -> None:
    for command in (
        "/admins",
        "/addadmin",
        "/deladmin",
        "/adddiscordadmin",
        "/deldiscordadmin",
        "/block",
        "/unblock",
        "/blocked",
        "/antiflood",
        "/ratelimit",
        "/host",
        "/host_consent",
        "/host_release",
        "/host_status",
        "/download",
    ):
        assert command in TELEGRAM_ADMIN_HELP
    assert "ADMIN_IDS" in TELEGRAM_ADMIN_HELP
    assert "OWNER_TELEGRAM_ID" in TELEGRAM_ADMIN_HELP
    assert "suggest-bot.zip" in DOWNLOAD_INSTRUCTIONS
    assert "/download" in TELEGRAM_ADMIN_HELP


def test_resolve_admin_services_from_attributes() -> None:
    container = SimpleNamespace(
        admins="a", blacklist="b", antiflood="c", guild_config="d"
    )
    resolved = resolve_admin_services(container)
    assert isinstance(resolved, AdminServices)
    assert (resolved.admins, resolved.guilds) == ("a", "d")


def test_resolve_admin_services_from_mapping_and_passthrough() -> None:
    mapping = {
        "admin_service": "a",
        "blacklist_service": "b",
        "antiflood_service": "c",
        "guilds": "d",
    }
    resolved = resolve_admin_services(mapping)
    assert resolved.antiflood == "c"
    assert resolve_admin_services(resolved) is resolved


def test_resolve_admin_services_reports_missing() -> None:
    with pytest.raises(TypeError) as exc:
        resolve_admin_services(SimpleNamespace(admins="a"))
    message = str(exc.value)
    assert "antiflood" in message
    assert "blacklist" in message
    assert "guilds" in message
