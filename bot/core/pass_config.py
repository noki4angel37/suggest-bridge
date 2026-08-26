"""Optional Discord slash command: request a temporary role via moderation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

DEFAULT_COMMAND = "проходка"
DEFAULT_LABEL = "проходка"
DEFAULT_ROLE_NAME = "проходка"
DEFAULT_CHANNEL_NAME = "проходка"
DEFAULT_CATEGORY_NAME = "закрытые каналы"
DEFAULT_DURATION_SEC = 5 * 60 * 60
DEFAULT_REJECT_COOLDOWN_SEC = 5 * 60
DEFAULT_ANTISPAM_LIMIT = 5
DEFAULT_ANTISPAM_WINDOW_SEC = 10 * 60
DEFAULT_ANTISPAM_STRIKE_SEC = 15 * 60
DEFAULT_DEBOUNCE_SEC = 8
DEFAULT_EXPIRY_POLL_SEC = 30.0


class _SettingsStore(Protocol):
    def get_setting(self, key: str, default: str | None = None) -> str | None: ...


def pass_role_setting_key(guild_id: str) -> str:
    return f"discord_pass_role:{guild_id}"


def _optional_id(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value or value == "REPLACE_ME":
        return None
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _clean_id(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped or stripped == "REPLACE_ME":
        return None
    return stripped


def resolve_pass_role_id(
    db: _SettingsStore, guild_id: str, config: PassConfig
) -> str | None:
    """Guild setting from /setup_pass wins over DISCORD_PASS_ROLE_ID."""
    stored = _clean_id(db.get_setting(pass_role_setting_key(guild_id)))
    if stored:
        return stored
    return config.role_id


@dataclass(frozen=True)
class PassConfig:
    role_id: str | None
    command: str = DEFAULT_COMMAND
    label: str = DEFAULT_LABEL
    role_name: str = DEFAULT_ROLE_NAME
    channel_name: str = DEFAULT_CHANNEL_NAME
    category_name: str = DEFAULT_CATEGORY_NAME
    duration_sec: int = DEFAULT_DURATION_SEC
    reject_cooldown_sec: int = DEFAULT_REJECT_COOLDOWN_SEC
    antispam_limit: int = DEFAULT_ANTISPAM_LIMIT
    antispam_window_sec: int = DEFAULT_ANTISPAM_WINDOW_SEC
    antispam_strike_sec: int = DEFAULT_ANTISPAM_STRIKE_SEC
    debounce_sec: int = DEFAULT_DEBOUNCE_SEC
    expiry_poll_sec: float = DEFAULT_EXPIRY_POLL_SEC

    @property
    def enabled(self) -> bool:
        return bool(self.role_id)


def load_pass_config() -> PassConfig:
    command = (os.environ.get("DISCORD_PASS_COMMAND") or DEFAULT_COMMAND).strip()
    label = (os.environ.get("DISCORD_PASS_LABEL") or DEFAULT_LABEL).strip()
    role_name = (os.environ.get("DISCORD_PASS_ROLE_NAME") or label or DEFAULT_ROLE_NAME).strip()
    channel_name = (
        os.environ.get("DISCORD_PASS_CHANNEL_NAME") or DEFAULT_CHANNEL_NAME
    ).strip()
    category_name = (
        os.environ.get("DISCORD_PASS_CATEGORY_NAME") or DEFAULT_CATEGORY_NAME
    ).strip()
    return PassConfig(
        role_id=_optional_id("DISCORD_PASS_ROLE_ID"),
        command=command or DEFAULT_COMMAND,
        label=label or DEFAULT_LABEL,
        role_name=role_name or DEFAULT_ROLE_NAME,
        channel_name=channel_name or DEFAULT_CHANNEL_NAME,
        category_name=category_name or DEFAULT_CATEGORY_NAME,
        duration_sec=_int_env("DISCORD_PASS_DURATION_SEC", DEFAULT_DURATION_SEC),
        reject_cooldown_sec=_int_env(
            "DISCORD_PASS_REJECT_COOLDOWN_SEC", DEFAULT_REJECT_COOLDOWN_SEC
        ),
        antispam_limit=_int_env(
            "DISCORD_PASS_ANTISPAM_LIMIT", DEFAULT_ANTISPAM_LIMIT
        ),
        antispam_window_sec=_int_env(
            "DISCORD_PASS_ANTISPAM_WINDOW_SEC", DEFAULT_ANTISPAM_WINDOW_SEC
        ),
        antispam_strike_sec=_int_env(
            "DISCORD_PASS_ANTISPAM_STRIKE_SEC", DEFAULT_ANTISPAM_STRIKE_SEC
        ),
        debounce_sec=_int_env("DISCORD_PASS_DEBOUNCE_SEC", DEFAULT_DEBOUNCE_SEC),
        expiry_poll_sec=_float_env(
            "DISCORD_PASS_EXPIRY_POLL_SEC", DEFAULT_EXPIRY_POLL_SEC
        ),
    )
