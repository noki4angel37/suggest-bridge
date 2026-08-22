from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from bot.core.db import resolve_bridge_db_path


class RunMode(str, Enum):
    """Which platforms this process serves."""

    telegram_only = "telegram_only"
    discord_only = "discord_only"
    both = "both"


@dataclass(frozen=True)
class BridgeConfig:
    """Settings of the single-process bridge (Telegram + Discord + scheduler)."""

    run_mode: RunMode
    bridge_db_path: str
    bot_token: str | None = None
    admin_ids: frozenset[int] = frozenset()
    channel_id: int | None = None
    discord_token: str | None = None

    @property
    def telegram_enabled(self) -> bool:
        return self.run_mode in {RunMode.telegram_only, RunMode.both}

    @property
    def discord_enabled(self) -> bool:
        return self.run_mode in {RunMode.discord_only, RunMode.both}


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def bootstrap_env() -> None:
    """Load `.env`, `local.env`, or `BOT_ENV_FILE` without overriding existing env."""
    root = Path(__file__).resolve().parent.parent
    custom = os.environ.get("BOT_ENV_FILE", "").strip()
    if custom:
        _load_env_file(Path(custom))
    for name in (".env", "local.env"):
        _load_env_file(root / name)


def _read_setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value == "REPLACE_ME":
        raise RuntimeError(f"{name} is not set")
    return value


def _optional_setting(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value or value == "REPLACE_ME":
        return None
    return value


def _parse_admin_ids(raw: str) -> frozenset[int]:
    admin_ids = frozenset(int(x.strip()) for x in raw.split(",") if x.strip())
    if not admin_ids:
        raise RuntimeError("ADMIN_IDS is empty")
    return admin_ids


def _has_telegram_credentials() -> bool:
    return _optional_setting("BOT_TOKEN") is not None


def _has_discord_credentials() -> bool:
    return _optional_setting("DISCORD_TOKEN") is not None


def detect_run_mode() -> RunMode:
    has_tg = _has_telegram_credentials()
    has_ds = _has_discord_credentials()
    if has_tg and has_ds:
        return RunMode.both
    if has_tg:
        return RunMode.telegram_only
    if has_ds:
        return RunMode.discord_only
    raise RuntimeError(
        "Set BOT_TOKEN (Telegram) and/or DISCORD_TOKEN (Discord). "
        "See .env.example."
    )


def load_bridge_config() -> BridgeConfig:
    """Bridge settings; tokens depend on run mode."""
    bootstrap_env()
    mode = detect_run_mode()
    bridge_db_path = resolve_bridge_db_path()
    discord_token = _optional_setting("DISCORD_TOKEN")
    bot_token: str | None = None
    admin_ids: frozenset[int] = frozenset()
    channel_id: int | None = None

    if mode in {RunMode.telegram_only, RunMode.both}:
        bot_token = _read_setting("BOT_TOKEN")
        admin_ids = _parse_admin_ids(_read_setting("ADMIN_IDS"))
        channel_id = int(_read_setting("CHANNEL_ID"))

    if mode is RunMode.discord_only:
        if not _optional_setting("OWNER_DISCORD_ID"):
            raise RuntimeError(
                "Discord-only mode requires OWNER_DISCORD_ID "
                "(your Discord user id for bootstrap admin)."
            )

    if mode in {RunMode.both, RunMode.discord_only}:
        discord_token = _read_setting("DISCORD_TOKEN")

    return BridgeConfig(
        run_mode=mode,
        bridge_db_path=bridge_db_path,
        bot_token=bot_token,
        admin_ids=admin_ids,
        channel_id=channel_id,
        discord_token=discord_token,
    )
