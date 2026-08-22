"""Lightweight host-sync folder protocol (Syncthing-friendly JSON files).

Default root: %LOCALAPPDATA%/suggest-host-sync on Windows, else
~/.local/share/suggest-host-sync. Override with HOST_SYNC_DIR.

Layout:
  registry/{host_id}.json  — agent heartbeat / capabilities
  state.json               — primary mirror of control plane
  commands/{host_id}.json  — primary → agent
  acks/{host_id}.json      — agent → primary
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SAFE_HOST_RE = re.compile(r"[^A-Za-z0-9._@+-]+")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(moment: datetime | None = None) -> str:
    moment = moment or _utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def resolve_sync_dir() -> Path:
    raw = os.environ.get("HOST_SYNC_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "suggest-host-sync"
    return Path.home() / ".local" / "share" / "suggest-host-sync"


def safe_host_filename(host_id: str) -> str:
    # Windows forbids ':' in filenames (drive syntax); map to '~' first.
    cleaned = host_id.strip().replace(":", "~")
    cleaned = SAFE_HOST_RE.sub("_", cleaned) or "unknown-host"
    return cleaned[:180]


def ensure_sync_tree(root: Path | None = None) -> Path:
    base = root or resolve_sync_dir()
    for name in ("registry", "commands", "acks"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return base


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Не удалось прочитать %s", path)
        return None
    return data if isinstance(data, dict) else None


@dataclass
class HostRegistryEntry:
    host_id: str
    admin_telegram_id: str | None = None
    admin_discord_id: str | None = None
    has_discord: bool = False
    agent_online: bool = True
    last_seen: str = field(default_factory=_to_iso)
    os_name: str = ""
    bot_role: str | None = None  # primary | standby | stopped

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HostRegistryEntry:
        return cls(
            host_id=str(data.get("host_id") or ""),
            admin_telegram_id=_opt_str(data.get("admin_telegram_id")),
            admin_discord_id=_opt_str(data.get("admin_discord_id")),
            has_discord=bool(data.get("has_discord")),
            agent_online=bool(data.get("agent_online", True)),
            last_seen=str(data.get("last_seen") or _to_iso()),
            os_name=str(data.get("os_name") or ""),
            bot_role=_opt_str(data.get("bot_role")),
        )


@dataclass
class HostCommand:
    action: str  # prepare | go_primary | stop | start
    request_id: str | None = None
    issued_at: str = field(default_factory=_to_iso)
    issued_by: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "request_id": self.request_id,
            "issued_at": self.issued_at,
            "issued_by": self.issued_by,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HostCommand:
        payload = data.get("payload")
        return cls(
            action=str(data.get("action") or ""),
            request_id=_opt_str(data.get("request_id")),
            issued_at=str(data.get("issued_at") or _to_iso()),
            issued_by=_opt_str(data.get("issued_by")),
            payload=payload if isinstance(payload, dict) else {},
        )


@dataclass
class HostAck:
    action: str
    ok: bool
    detail: str = ""
    request_id: str | None = None
    acked_at: str = field(default_factory=_to_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HostAck:
        return cls(
            action=str(data.get("action") or ""),
            ok=bool(data.get("ok")),
            detail=str(data.get("detail") or ""),
            request_id=_opt_str(data.get("request_id")),
            acked_at=str(data.get("acked_at") or _to_iso()),
        )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class HostSyncStore:
    """Read/write helpers for the shared host-sync directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = ensure_sync_tree(root)

    def registry_path(self, host_id: str) -> Path:
        return self.root / "registry" / f"{safe_host_filename(host_id)}.json"

    def command_path(self, host_id: str) -> Path:
        return self.root / "commands" / f"{safe_host_filename(host_id)}.json"

    def ack_path(self, host_id: str) -> Path:
        return self.root / "acks" / f"{safe_host_filename(host_id)}.json"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def write_registry(self, entry: HostRegistryEntry) -> None:
        _atomic_write_json(self.registry_path(entry.host_id), entry.to_dict())

    def read_registry(self, host_id: str) -> HostRegistryEntry | None:
        data = _read_json(self.registry_path(host_id))
        return HostRegistryEntry.from_dict(data) if data else None

    def list_registry(self) -> list[HostRegistryEntry]:
        folder = self.root / "registry"
        if not folder.is_dir():
            return []
        out: list[HostRegistryEntry] = []
        for path in sorted(folder.glob("*.json")):
            data = _read_json(path)
            if data:
                out.append(HostRegistryEntry.from_dict(data))
        return out

    def write_state(self, payload: dict[str, Any]) -> None:
        _atomic_write_json(self.state_path, payload)

    def read_state(self) -> dict[str, Any] | None:
        return _read_json(self.state_path)

    def write_command(self, host_id: str, command: HostCommand) -> None:
        _atomic_write_json(self.command_path(host_id), command.to_dict())

    def read_command(self, host_id: str) -> HostCommand | None:
        data = _read_json(self.command_path(host_id))
        return HostCommand.from_dict(data) if data else None

    def clear_command(self, host_id: str) -> None:
        path = self.command_path(host_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Не удалось удалить команду %s", path)

    def write_ack(self, host_id: str, ack: HostAck) -> None:
        _atomic_write_json(self.ack_path(host_id), ack.to_dict())

    def read_ack(self, host_id: str) -> HostAck | None:
        data = _read_json(self.ack_path(host_id))
        return HostAck.from_dict(data) if data else None

    def clear_ack(self, host_id: str) -> None:
        path = self.ack_path(host_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Не удалось удалить ack %s", path)
