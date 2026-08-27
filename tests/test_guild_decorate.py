"""Unit tests for guild decorate helpers and publish ACL."""

from __future__ import annotations

from types import SimpleNamespace

from bot.adapters.discord import texts
from bot.adapters.discord.guild_decorate import (
    channel_slug,
    decorated_name,
    is_mod_channel_name,
    is_publish_channel_name,
    is_suggest_channel_name,
    matches_slug,
    spec_for_channel_name,
)
from bot.adapters.discord.guild_setup import publish_channel_overwrites


def test_channel_slug_strips_emoji_prefix() -> None:
    assert channel_slug("📨┃предложка") == "предложка"
    assert channel_slug("📰│новости-сервера") == "новости-сервера"
    assert channel_slug("посты-предложения") == "посты-предложения"
    assert channel_slug("┃администратоство") == "администратоство"


def test_matches_slug_casefold() -> None:
    assert matches_slug("💡┃General", "general")
    assert not matches_slug("💡┃general", "предложка")


def test_suggest_vs_publish_names() -> None:
    assert is_suggest_channel_name("💡┃посты-предложения")
    assert is_suggest_channel_name(texts.SETUP_SUGGEST_CHANNEL_NAME)
    assert not is_suggest_channel_name("📨┃предложка")
    assert not is_suggest_channel_name("предложка")

    assert is_publish_channel_name("📨┃предложка")
    assert is_publish_channel_name("предложка")
    assert is_publish_channel_name("📦┃посты-опубликованно")
    assert is_publish_channel_name("📦┃посты-опубликовано")
    assert is_mod_channel_name("🛡️┃модерация-предложки")


def test_decorated_name_and_spec() -> None:
    assert decorated_name("📨", "предложка") == "📨┃предложка"
    spec = spec_for_channel_name("предложка")
    assert spec is not None
    assert spec.slug == "предложка"
    assert spec.readonly is True
    legacy = spec_for_channel_name("посты-опубликованно")
    assert legacy is not None
    assert legacy.slug == "посты-опубликовано"
    assert legacy.readonly is True
    assert spec_for_channel_name("посты-опубликовано") is legacy


def test_publish_overwrites_deny_everyone_send() -> None:
    class _Target:
        def __init__(self, id_: int) -> None:
            self.id = id_

        def __hash__(self) -> int:
            return hash(self.id)

        def __eq__(self, other: object) -> bool:
            return isinstance(other, _Target) and other.id == self.id

    everyone = _Target(1)
    editor = _Target(2)
    me = _Target(3)
    guild = SimpleNamespace(default_role=everyone, me=me)

    overwrites = publish_channel_overwrites(guild, editor)  # type: ignore[arg-type]
    assert overwrites[everyone].send_messages is False
    assert overwrites[everyone].attach_files is False
    assert overwrites[everyone].create_public_threads is False
    assert overwrites[everyone].send_messages_in_threads is False
    assert overwrites[editor].send_messages is True
    assert overwrites[editor].manage_messages is True
    assert overwrites[me].send_messages is True
    assert overwrites[me].manage_messages is True


def test_decorate_done_text() -> None:
    msg = texts.decorate_done(
        renamed=3,
        moved=2,
        locked=5,
        publish_mention="#предложка",
        errors=["x"],
    )
    assert "Переименовано: 3" in msg
    assert "#предложка" in msg
    assert "x" in msg
