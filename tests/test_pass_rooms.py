from __future__ import annotations

from bot.adapters.discord.pass_rooms import find_category, find_role_by_name, find_text_channel
from bot.core.pass_config import (
    DEFAULT_CATEGORY_NAME,
    DEFAULT_CHANNEL_NAME,
    DEFAULT_LABEL,
    pass_role_setting_key,
)


def test_pass_role_setting_key() -> None:
    assert pass_role_setting_key("200") == "discord_pass_role:200"


def test_generic_defaults_are_not_theme_specific() -> None:
    assert DEFAULT_LABEL == "проходка"
    assert DEFAULT_CHANNEL_NAME == "проходка"
    assert DEFAULT_CATEGORY_NAME == "закрытые каналы"
    assert "казино" not in DEFAULT_LABEL.casefold()
    assert "казино" not in DEFAULT_CATEGORY_NAME.casefold()


class _Role:
    def __init__(self, name: str) -> None:
        self.name = name


class _Channel:
    def __init__(
        self, name: str, *, category_id: int | None = None, id: int = 1
    ) -> None:
        self.name = name
        self.category_id = category_id
        self.id = id


class _Category:
    def __init__(self, name: str, *, id: int = 9) -> None:
        self.name = name
        self.id = id


class _Guild:
    def __init__(self) -> None:
        self.roles = [_Role("@everyone"), _Role("проходка"), _Role("мод")]
        self.text_channels = [
            _Channel("общий", category_id=1),
            _Channel("проходка", category_id=9),
            _Channel("чат", category_id=9),
        ]
        self.categories = [_Category("ПРЕДЛОЖКИ", id=1), _Category("закрытые каналы", id=9)]


def test_find_role_by_name() -> None:
    guild = _Guild()
    found = find_role_by_name(guild, "Проходка")  # type: ignore[arg-type]
    assert found is not None
    assert found.name == "проходка"
    assert find_role_by_name(guild, "нет-такой") is None  # type: ignore[arg-type]


def test_find_text_channel_prefers_category() -> None:
    guild = _Guild()
    category = guild.categories[1]
    found = find_text_channel(guild, "проходка", category=category)  # type: ignore[arg-type]
    assert found is not None
    assert found.category_id == 9
    other = find_text_channel(guild, "чат")  # type: ignore[arg-type]
    assert other is not None
    assert other.name == "чат"


def test_find_category() -> None:
    guild = _Guild()
    found = find_category(guild, "закрытые каналы")  # type: ignore[arg-type]
    assert found is not None
    assert found.id == 9
    assert find_category(guild, "нет") is None  # type: ignore[arg-type]
