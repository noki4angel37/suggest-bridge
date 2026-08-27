"""Product settings loaded from environment (with sensible Russian defaults)."""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def suggest_hashtag() -> str:
    return _env("SUGGEST_HASHTAG", "#предложка")


def anon_name() -> str:
    return _env("ANON_NAME", "Аноним")


def setup_category_name() -> str:
    return _env("SETUP_CATEGORY_NAME", "ПРЕДЛОЖКИ")


def setup_suggest_channel_name() -> str:
    return _env("SETUP_SUGGEST_CHANNEL_NAME", "посты-предложения")


def setup_mod_channel_name() -> str:
    return _env("SETUP_MOD_CHANNEL_NAME", "модерация-предложки")


def setup_publish_channel_name() -> str:
    return _env("SETUP_PUBLISH_CHANNEL_NAME", "предложка")


def setup_publish_channel_aliases() -> tuple[str, ...]:
    raw = _env(
        "SETUP_PUBLISH_CHANNEL_ALIASES",
        "посты-опубликованно,посты-опубликовано",
    )
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def setup_editor_role_name() -> str:
    return _env("SETUP_EDITOR_ROLE_NAME", "недоадмин")


def keyword_blocklist() -> tuple[str, ...]:
    """CSV from KEYWORD_BLOCKLIST — reject submissions containing any token."""
    from bot.core.rules import parse_keyword_blocklist

    return parse_keyword_blocklist(os.environ.get("KEYWORD_BLOCKLIST", ""))


def admin_chat_id() -> int | None:
    """Optional Telegram group/supergroup for moderation cards (not DMs)."""
    raw = os.environ.get("ADMIN_CHAT_ID", "").strip()
    if not raw or raw == "REPLACE_ME":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def discord_mod_log_channel_id() -> str | None:
    raw = os.environ.get("DISCORD_MOD_LOG_CHANNEL_ID", "").strip()
    if not raw or raw == "REPLACE_ME":
        return None
    return raw


def discord_publish_threads() -> bool:
    raw = os.environ.get("DISCORD_PUBLISH_THREADS", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
