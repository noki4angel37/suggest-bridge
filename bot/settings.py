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
    raw = _env("SETUP_PUBLISH_CHANNEL_ALIASES", "посты-опубликованно")
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def setup_editor_role_name() -> str:
    return _env("SETUP_EDITOR_ROLE_NAME", "недоадмин")
