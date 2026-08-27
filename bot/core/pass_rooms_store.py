"""Guild registry of pass-gated Discord rooms (settings JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

PASS_MODE_HIDE = "hide"
PASS_MODE_VISIBLE = "visible"
PASS_KIND_TEXT = "text"
PASS_KIND_VOICE = "voice"

_VALID_MODES = frozenset({PASS_MODE_HIDE, PASS_MODE_VISIBLE})
_VALID_KINDS = frozenset({PASS_KIND_TEXT, PASS_KIND_VOICE})


class _SettingsStore(Protocol):
    def get_setting(self, key: str, default: str | None = None) -> str | None: ...

    def set_setting(self, key: str, value: str | None) -> None: ...


@dataclass(frozen=True)
class PassRoomEntry:
    channel_id: str
    mode: str
    kind: str


def pass_rooms_setting_key(guild_id: str) -> str:
    return f"discord_pass_rooms:{guild_id}"


def normalize_pass_mode(value: str | None, *, default: str = PASS_MODE_HIDE) -> str:
    cleaned = (value or "").strip().casefold()
    if cleaned in _VALID_MODES:
        return cleaned
    return default


def normalize_pass_kind(value: str | None, *, default: str = PASS_KIND_TEXT) -> str:
    cleaned = (value or "").strip().casefold()
    if cleaned in _VALID_KINDS:
        return cleaned
    return default


def _entry_from_raw(raw: object) -> PassRoomEntry | None:
    if not isinstance(raw, dict):
        return None
    channel_id = str(raw.get("channel_id") or "").strip()
    if not channel_id:
        return None
    mode = normalize_pass_mode(str(raw.get("mode") or ""), default="")
    kind = normalize_pass_kind(str(raw.get("kind") or ""), default="")
    if mode not in _VALID_MODES or kind not in _VALID_KINDS:
        return None
    return PassRoomEntry(channel_id=channel_id, mode=mode, kind=kind)


def list_pass_rooms(db: _SettingsStore, guild_id: str) -> list[PassRoomEntry]:
    raw = db.get_setting(pass_rooms_setting_key(guild_id))
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    rooms: list[PassRoomEntry] = []
    seen: set[str] = set()
    for item in data:
        entry = _entry_from_raw(item)
        if entry is None or entry.channel_id in seen:
            continue
        seen.add(entry.channel_id)
        rooms.append(entry)
    return rooms


def get_pass_room(
    db: _SettingsStore, guild_id: str, channel_id: str | int
) -> PassRoomEntry | None:
    wanted = str(channel_id).strip()
    if not wanted:
        return None
    for entry in list_pass_rooms(db, guild_id):
        if entry.channel_id == wanted:
            return entry
    return None


def _dump_rooms(db: _SettingsStore, guild_id: str, rooms: list[PassRoomEntry]) -> None:
    payload = [
        {"channel_id": room.channel_id, "mode": room.mode, "kind": room.kind}
        for room in rooms
    ]
    db.set_setting(pass_rooms_setting_key(guild_id), json.dumps(payload, ensure_ascii=False))


def upsert_pass_room(
    db: _SettingsStore,
    guild_id: str,
    channel_id: str | int,
    *,
    mode: str,
    kind: str,
) -> PassRoomEntry:
    cid = str(channel_id).strip()
    if not cid:
        raise ValueError("channel_id is required")
    entry = PassRoomEntry(
        channel_id=cid,
        mode=normalize_pass_mode(mode),
        kind=normalize_pass_kind(kind),
    )
    rooms = [room for room in list_pass_rooms(db, guild_id) if room.channel_id != cid]
    rooms.append(entry)
    _dump_rooms(db, guild_id, rooms)
    return entry


def remove_pass_room(
    db: _SettingsStore, guild_id: str, channel_id: str | int
) -> bool:
    wanted = str(channel_id).strip()
    if not wanted:
        return False
    rooms = list_pass_rooms(db, guild_id)
    kept = [room for room in rooms if room.channel_id != wanted]
    if len(kept) == len(rooms):
        return False
    _dump_rooms(db, guild_id, kept)
    return True
