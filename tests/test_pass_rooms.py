from __future__ import annotations

import discord

from bot.adapters.discord.pass_rooms import (
    find_category,
    find_role_by_name,
    find_text_channel,
    find_voice_channel,
    pass_channel_overwrites,
    pass_room_overwrites,
)
from bot.core.pass_config import (
    DEFAULT_CATEGORY_NAME,
    DEFAULT_CHANNEL_NAME,
    DEFAULT_LABEL,
    pass_role_setting_key,
)
from bot.core.pass_rooms_store import (
    PASS_KIND_TEXT,
    PASS_KIND_VOICE,
    PASS_MODE_HIDE,
    PASS_MODE_VISIBLE,
    get_pass_room,
    list_pass_rooms,
    normalize_pass_kind,
    normalize_pass_mode,
    pass_rooms_setting_key,
    remove_pass_room,
    upsert_pass_room,
)


def test_pass_role_setting_key() -> None:
    assert pass_role_setting_key("200") == "discord_pass_role:200"


def test_pass_rooms_setting_key() -> None:
    assert pass_rooms_setting_key("200") == "discord_pass_rooms:200"


def test_generic_defaults_are_not_theme_specific() -> None:
    assert DEFAULT_LABEL == "проходка"
    assert DEFAULT_CHANNEL_NAME == "проходка"
    assert DEFAULT_CATEGORY_NAME == "закрытые каналы"
    assert "казино" not in DEFAULT_LABEL.casefold()
    assert "казино" not in DEFAULT_CATEGORY_NAME.casefold()
    assert "казино" not in DEFAULT_CHANNEL_NAME.casefold()


def test_normalize_mode_and_kind_defaults() -> None:
    assert normalize_pass_mode(None) == PASS_MODE_HIDE
    assert normalize_pass_mode("видно") == PASS_MODE_HIDE  # value must be hide|visible
    assert normalize_pass_mode("visible") == PASS_MODE_VISIBLE
    assert normalize_pass_kind(None) == PASS_KIND_TEXT
    assert normalize_pass_kind("voice") == PASS_KIND_VOICE


class _MemorySettings:
    def __init__(self) -> None:
        self._data: dict[str, str | None] = {}

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key, default)

    def set_setting(self, key: str, value: str | None) -> None:
        self._data[key] = value


def test_pass_rooms_store_upsert_list_get_remove() -> None:
    db = _MemorySettings()
    upsert_pass_room(db, "1", 10, mode=PASS_MODE_HIDE, kind=PASS_KIND_TEXT)
    upsert_pass_room(db, "1", 20, mode=PASS_MODE_VISIBLE, kind=PASS_KIND_TEXT)
    upsert_pass_room(db, "1", 30, mode=PASS_MODE_HIDE, kind=PASS_KIND_VOICE)
    rooms = list_pass_rooms(db, "1")
    assert [(r.channel_id, r.mode, r.kind) for r in rooms] == [
        ("10", PASS_MODE_HIDE, PASS_KIND_TEXT),
        ("20", PASS_MODE_VISIBLE, PASS_KIND_TEXT),
        ("30", PASS_MODE_HIDE, PASS_KIND_VOICE),
    ]
    upsert_pass_room(db, "1", 20, mode=PASS_MODE_HIDE, kind=PASS_KIND_VOICE)
    updated = get_pass_room(db, "1", 20)
    assert updated is not None
    assert updated.mode == PASS_MODE_HIDE
    assert updated.kind == PASS_KIND_VOICE
    assert remove_pass_room(db, "1", 10) is True
    assert get_pass_room(db, "1", 10) is None
    assert remove_pass_room(db, "1", 10) is False


def test_pass_rooms_store_ignores_corrupt_json() -> None:
    db = _MemorySettings()
    db.set_setting(pass_rooms_setting_key("9"), "{not-json")
    assert list_pass_rooms(db, "9") == []
    db.set_setting(
        pass_rooms_setting_key("9"),
        '[{"channel_id":"1","mode":"nope","kind":"text"}]',
    )
    assert list_pass_rooms(db, "9") == []


class _Role:
    def __init__(self, name: str, *, id: int = 1) -> None:
        self.name = name
        self.id = id
        self.mention = f"<@&{id}>"


class _Member:
    def __init__(self, *, id: int = 99) -> None:
        self.id = id


class _Guild:
    def __init__(self) -> None:
        self.default_role = _Role("@everyone", id=0)
        self.me = _Member(id=42)
        self.roles = [_Role("@everyone"), _Role("проходка"), _Role("мод")]
        self.text_channels = [
            _Channel("общий", category_id=1),
            _Channel("проходка", category_id=9),
            _Channel("чат", category_id=9),
        ]
        self.voice_channels = [
            _Channel("общий-войс", category_id=1, id=50),
            _Channel("проходка", category_id=9, id=51),
        ]
        self.categories = [
            _Category("ПРЕДЛОЖКИ", id=1),
            _Category("закрытые каналы", id=9),
        ]


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


def test_find_voice_channel() -> None:
    guild = _Guild()
    category = guild.categories[1]
    found = find_voice_channel(guild, "проходка", category=category)  # type: ignore[arg-type]
    assert found is not None
    assert found.id == 51


def test_find_category() -> None:
    guild = _Guild()
    found = find_category(guild, "закрытые каналы")  # type: ignore[arg-type]
    assert found is not None
    assert found.id == 9
    assert find_category(guild, "нет") is None  # type: ignore[arg-type]


def _pair(
    overwrites: dict, target: object
) -> discord.PermissionOverwrite:
    return overwrites[target]


def test_hide_text_overwrites() -> None:
    guild = _Guild()
    role = _Role("проходка", id=7)
    overwrites = pass_channel_overwrites(guild, role)  # type: ignore[arg-type]
    everyone = _pair(overwrites, guild.default_role)
    assert everyone.view_channel is False
    assert everyone.send_messages is False
    pass_ow = _pair(overwrites, role)
    assert pass_ow.view_channel is True
    assert pass_ow.send_messages is True
    bot_ow = _pair(overwrites, guild.me)
    assert bot_ow.manage_messages is True


def test_visible_text_overwrites() -> None:
    guild = _Guild()
    role = _Role("проходка", id=7)
    overwrites = pass_room_overwrites(
        guild, role, mode=PASS_MODE_VISIBLE, kind=PASS_KIND_TEXT  # type: ignore[arg-type]
    )
    everyone = _pair(overwrites, guild.default_role)
    assert everyone.view_channel is True
    assert everyone.send_messages is True
    bot_ow = _pair(overwrites, guild.me)
    assert bot_ow.manage_messages is True


def test_voice_overwrites() -> None:
    guild = _Guild()
    role = _Role("проходка", id=7)
    overwrites = pass_room_overwrites(
        guild, role, mode=PASS_MODE_HIDE, kind=PASS_KIND_VOICE  # type: ignore[arg-type]
    )
    everyone = _pair(overwrites, guild.default_role)
    assert everyone.view_channel is True
    assert everyone.connect is False
    pass_ow = _pair(overwrites, role)
    assert pass_ow.connect is True
    assert pass_ow.speak is True
