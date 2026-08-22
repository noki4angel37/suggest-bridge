"""Multi-PC host control: lease + transfer requests + force + panel state.

Builds on settings in bridge.db and mirrors a light view into host-sync files.
Legacy consent/claim APIs remain in host_lease.py and are re-exported here for
the control plane helpers.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from bot.core.db import BridgeDatabase
from bot.core.host_lease import (
    EXIT_CONSENT_DENIED,
    EXIT_LEASE_HELD,
    ConsentDeniedError,
    HostLeaseError,
    HostLeaseStatus,
    LeaseHeldError,
    claim,
    grant_consent,
    heartbeat_loop,
    is_primary,
    release,
    renew,
    require_consent,
    resolve_host_id,
    revoke_consent,
    status as lease_status,
)
from bot.core.host_sync import (
    HostAck,
    HostCommand,
    HostRegistryEntry,
    HostSyncStore,
    resolve_sync_dir,
)

logger = logging.getLogger(__name__)

# Fixed MSK offset — avoids tzdata dependency on Windows.
MSK = timezone(timedelta(hours=3), name="MSK")

KEY_STARTED_AT = "host_started_at"
KEY_HOLDER_ADMIN = "host_holder_admin"
KEY_PENDING = "host_pending_json"
KEY_AUDIT = "host_audit_json"
KEY_COOLDOWN = "host_request_cooldown_json"

DEFAULT_AGENT_ONLINE_SEC = 180
DEFAULT_REQUEST_COOLDOWN_SEC = 45
DEFAULT_HANDOVER_TTL_SEC = 90
DEFAULT_AUDIT_TAIL = 40

INSTALL_HINT = (
    "Агент не найден на этом ПК.\n"
    "Установка с нуля (Windows):\n"
    "1) В личке бота: /download — инструкция и zip "
    "(Discord: /download_bot)\n"
    "2) Распакуйте и запустите: .\\install-agent.ps1\n"
    "3) Заполните local.env "
    "(BOT_TOKEN, DISCORD_TOKEN, ADMIN_IDS, OWNER_*)\n"
    "4) Syncthing: folder id suggest-host-sync → "
    "%LOCALAPPDATA%\\suggest-host-sync\n"
    "5) .\\run-agent.ps1 → дождитесь ПК в /host и повторите"
)


class HostControlError(Exception):
    """Safe Russian message for admins (no secrets)."""


@dataclass
class TransferRequest:
    id: str
    kind: str  # claim | offer
    from_admin: str
    from_host: str | None
    to_admin: str | None
    to_host: str
    status: str = "pending"  # pending | accepted | rejected | cancelled | expired
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransferRequest:
        return cls(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or "claim"),
            from_admin=str(data.get("from_admin") or ""),
            from_host=_opt(data.get("from_host")),
            to_admin=_opt(data.get("to_admin")),
            to_host=str(data.get("to_host") or ""),
            status=str(data.get("status") or "pending"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass
class AuditEntry:
    at: str
    actor: str
    action: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HostPanelSnapshot:
    host_id: str
    primary_id: str | None
    holder_admin: str | None
    started_at: datetime | None
    lease_active: bool
    is_primary: bool
    os_name: str | None
    has_discord: bool | None
    features: str
    pending: list[TransferRequest] = field(default_factory=list)
    registry: list[HostRegistryEntry] = field(default_factory=list)
    status_label: str = "не запущен"

    def format_msk(self) -> str:
        """Public status for admins — no machine names / HOST_ID / OS hostname."""
        lines = [
            "Статус бота (время МСК)",
            f"Состояние: {self.status_label}",
        ]
        if self.lease_active and self.primary_id:
            lines.append(f"Держит админ: {self.holder_admin or '—'}")
            lines.append(f"Запущен: {_fmt_msk(self.started_at)}")
            lines.append(f"Uptime: {_uptime_label(self.started_at)}")
            lines.append(f"Возможности: {self.features}")
        if self.pending:
            lines.append("")
            lines.append("Ожидают передачи:")
            for req in self.pending:
                lines.append(
                    f"• [{req.kind}] {req.id[:8]}… "
                    f"от {req.from_admin} ({req.status})"
                )
        else:
            lines.append("")
            lines.append("Ожидающих запросов нет.")
        lines.append("")
        lines.append("Как запустить на другом ПК:")
        lines.append(
            "1) В личке: /download (zip + инструкция)\n"
            "2) .\\install-agent.ps1\n"
            "3) Заполните local.env (BOT_TOKEN, DISCORD_TOKEN, ADMIN_IDS)\n"
            "4) Syncthing: folder id suggest-host-sync\n"
            "5) .\\run-agent.ps1 → дождитесь ПК в /host"
        )
        return "\n".join(lines)

    def public_host_label(self, host_id: str) -> str:
        """Opaque label for UI buttons (never expose hostname/username)."""
        for index, entry in enumerate(self.registry, start=1):
            if entry.host_id == host_id:
                who = entry.admin_telegram_id or entry.admin_discord_id or "?"
                return f"ПК #{index} (админ {who})"
        return "ПК"


def _opt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(moment: datetime | None = None) -> str:
    moment = moment or _utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _fmt_msk(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    return moment.astimezone(MSK).strftime("%d.%m.%Y %H:%M:%S")


def _uptime_label(started: datetime | None) -> str:
    if started is None:
        return "—"
    delta = _utcnow() - started
    total = int(delta.total_seconds())
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}ч {minutes}м"
    if minutes:
        return f"{minutes}м {seconds}с"
    return f"{seconds}с"


def owner_telegram_id() -> str | None:
    raw = os.environ.get("OWNER_TELEGRAM_ID", "").strip()
    if raw:
        return raw
    admins = os.environ.get("ADMIN_IDS", "").strip()
    if admins:
        first = admins.split(",")[0].strip()
        if first:
            return first
    return None


def owner_discord_id() -> str | None:
    raw = os.environ.get("OWNER_DISCORD_ID", "").strip()
    if raw:
        return raw
    return None


def is_owner_telegram(user_id: str | int) -> bool:
    owner = owner_telegram_id()
    return owner is not None and str(user_id) == owner


def is_owner_discord(user_id: str | int) -> bool:
    owner = owner_discord_id()
    return owner is not None and str(user_id) == owner


def agent_online_sec() -> int:
    return _env_positive_int("HOST_AGENT_ONLINE_SEC", DEFAULT_AGENT_ONLINE_SEC)


def request_cooldown_sec() -> int:
    return _env_positive_int(
        "HOST_REQUEST_COOLDOWN_SEC", DEFAULT_REQUEST_COOLDOWN_SEC
    )


def handover_ttl_sec() -> int:
    return _env_positive_int("HOST_HANDOVER_TTL_SEC", DEFAULT_HANDOVER_TTL_SEC)


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _load_json_list(db: BridgeDatabase, key: str) -> list[dict[str, Any]]:
    raw = db.get_setting(key)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _save_json_list(
    db: BridgeDatabase, key: str, items: list[dict[str, Any]]
) -> None:
    db.set_setting(key, json.dumps(items, ensure_ascii=False))


def list_pending(db: BridgeDatabase) -> list[TransferRequest]:
    return [
        TransferRequest.from_dict(item)
        for item in _load_json_list(db, KEY_PENDING)
        if item.get("status") == "pending"
    ]


def list_all_requests(db: BridgeDatabase) -> list[TransferRequest]:
    return [
        TransferRequest.from_dict(item) for item in _load_json_list(db, KEY_PENDING)
    ]


def _write_requests(db: BridgeDatabase, items: list[TransferRequest]) -> None:
    _save_json_list(db, KEY_PENDING, [item.to_dict() for item in items])


def append_audit(
    db: BridgeDatabase,
    *,
    actor: str,
    action: str,
    detail: str = "",
) -> AuditEntry:
    entry = AuditEntry(at=_to_iso(), actor=actor, action=action, detail=detail)
    items = _load_json_list(db, KEY_AUDIT)
    items.append(entry.to_dict())
    items = items[-DEFAULT_AUDIT_TAIL:]
    _save_json_list(db, KEY_AUDIT, items)
    return entry


def list_audit(db: BridgeDatabase) -> list[AuditEntry]:
    return [AuditEntry(**item) for item in _load_json_list(db, KEY_AUDIT)]


def mark_primary_started(
    db: BridgeDatabase,
    host_id: str | None = None,
    *,
    holder_admin: str | None = None,
) -> None:
    hid = host_id or resolve_host_id()
    db.set_setting(KEY_STARTED_AT, _to_iso())
    if holder_admin:
        db.set_setting(KEY_HOLDER_ADMIN, str(holder_admin))
    logger.info("Primary started marker: HOST_ID=%s admin=%s", hid, holder_admin)


def clear_primary_markers(db: BridgeDatabase) -> None:
    db.delete_setting(KEY_STARTED_AT)
    db.delete_setting(KEY_HOLDER_ADMIN)


def force_claim(
    db: BridgeDatabase,
    host_id: str | None = None,
    *,
    holder_admin: str | None = None,
) -> HostLeaseStatus:
    """Steal primary for the new host (priority to new lease on conflict)."""
    from bot.core.host_lease import write_lease

    hid = host_id or resolve_host_id()
    write_lease(db, hid)
    if holder_admin:
        db.set_setting(KEY_HOLDER_ADMIN, str(holder_admin))
    if not db.get_setting(KEY_STARTED_AT):
        db.set_setting(KEY_STARTED_AT, _to_iso())
    return lease_status(db, hid)


def _cooldown_map(db: BridgeDatabase) -> dict[str, str]:
    raw = db.get_setting(KEY_COOLDOWN)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _touch_cooldown(db: BridgeDatabase, key: str) -> None:
    data = _cooldown_map(db)
    data[key] = _to_iso()
    db.set_setting(KEY_COOLDOWN, json.dumps(data))


def _check_cooldown(db: BridgeDatabase, key: str) -> None:
    data = _cooldown_map(db)
    last = _parse_iso(data.get(key))
    if last is None:
        return
    elapsed = (_utcnow() - last).total_seconds()
    need = request_cooldown_sec()
    if elapsed < need:
        left = int(need - elapsed)
        raise HostControlError(
            f"Слишком частые запросы. Подождите ещё {left} с."
        )


def entry_is_online(entry: HostRegistryEntry | None) -> bool:
    if entry is None or not entry.agent_online:
        return False
    seen = _parse_iso(entry.last_seen)
    if seen is None:
        return False
    return (_utcnow() - seen).total_seconds() <= agent_online_sec()


def find_registry_host(
    sync: HostSyncStore, host_id: str
) -> HostRegistryEntry | None:
    return sync.read_registry(host_id)


def find_hosts_for_admin(
    sync: HostSyncStore, *, telegram_id: str | None = None, discord_id: str | None = None
) -> list[HostRegistryEntry]:
    out: list[HostRegistryEntry] = []
    for entry in sync.list_registry():
        if telegram_id and entry.admin_telegram_id == str(telegram_id):
            out.append(entry)
        elif discord_id and entry.admin_discord_id == str(discord_id):
            out.append(entry)
    return out


def require_discord_capable(entry: HostRegistryEntry | None) -> HostRegistryEntry:
    if entry is None or not entry_is_online(entry):
        raise HostControlError(INSTALL_HINT)
    if not entry.has_discord:
        raise HostControlError(
            "На целевом ПК нет Discord (DISCORD_TOKEN). "
            "Перенос туда запрещён — иначе пропадёт DS-часть."
        )
    return entry


def create_claim_request(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    admin_id: str,
    target_host: str,
) -> TransferRequest:
    _check_cooldown(db, f"claim:{admin_id}")
    entry = require_discord_capable(find_registry_host(sync, target_host))
    if entry.admin_telegram_id and entry.admin_telegram_id != str(admin_id):
        # Allow owner to claim onto registered hosts they don't own.
        if not is_owner_telegram(admin_id):
            raise HostControlError(
                "Этот HOST_ID зарегистрирован за другим админом."
            )
    info = lease_status(db)
    if info.lease_active and info.primary_id == target_host:
        raise HostControlError("Этот хост уже primary.")

    now = _to_iso()
    req = TransferRequest(
        id=uuid.uuid4().hex,
        kind="claim",
        from_admin=str(admin_id),
        from_host=target_host,
        to_admin=info.holder_admin if hasattr(info, "holder_admin") else None,
        to_host=target_host,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    # Fix holder from settings
    req.to_admin = db.get_setting(KEY_HOLDER_ADMIN)
    items = list_all_requests(db)
    items.append(req)
    _write_requests(db, items)
    _touch_cooldown(db, f"claim:{admin_id}")
    append_audit(
        db,
        actor=f"tg:{admin_id}",
        action="claim_request",
        detail=f"host={target_host} id={req.id}",
    )
    mirror_state(db, sync)
    return req


def create_offer_request(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    from_admin: str,
    to_host: str,
    to_admin: str | None = None,
) -> TransferRequest:
    _check_cooldown(db, f"offer:{from_admin}")
    entry = require_discord_capable(find_registry_host(sync, to_host))
    now = _to_iso()
    req = TransferRequest(
        id=uuid.uuid4().hex,
        kind="offer",
        from_admin=str(from_admin),
        from_host=resolve_host_id(),
        to_admin=to_admin or entry.admin_telegram_id,
        to_host=to_host,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    items = list_all_requests(db)
    items.append(req)
    _write_requests(db, items)
    _touch_cooldown(db, f"offer:{from_admin}")
    append_audit(
        db,
        actor=f"tg:{from_admin}",
        action="offer_request",
        detail=f"to={to_host} id={req.id}",
    )
    mirror_state(db, sync)
    return req


def get_request(db: BridgeDatabase, request_id: str) -> TransferRequest | None:
    for item in list_all_requests(db):
        if item.id == request_id:
            return item
    return None


def _update_request(
    db: BridgeDatabase, request_id: str, **changes: Any
) -> TransferRequest:
    items = list_all_requests(db)
    found: TransferRequest | None = None
    for idx, item in enumerate(items):
        if item.id != request_id:
            continue
        data = item.to_dict()
        data.update(changes)
        data["updated_at"] = _to_iso()
        items[idx] = TransferRequest.from_dict(data)
        found = items[idx]
        break
    if found is None:
        raise HostControlError("Запрос не найден.")
    _write_requests(db, items)
    return found


def cancel_request(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    request_id: str,
    actor: str,
) -> TransferRequest:
    req = get_request(db, request_id)
    if req is None or req.status != "pending":
        raise HostControlError("Нечего отменять.")
    actor_id = actor.removeprefix("tg:").removeprefix("ds:")
    if req.from_admin != actor_id and not (
        is_owner_telegram(actor_id) or is_owner_discord(actor_id)
    ):
        raise HostControlError("Можно отменить только свой запрос.")
    updated = _update_request(db, request_id, status="cancelled")
    append_audit(db, actor=actor, action="cancel_request", detail=request_id)
    mirror_state(db, sync)
    return updated


def reject_request(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    request_id: str,
    actor: str,
) -> TransferRequest:
    req = get_request(db, request_id)
    if req is None or req.status != "pending":
        raise HostControlError("Запрос уже закрыт.")
    updated = _update_request(db, request_id, status="rejected")
    append_audit(db, actor=actor, action="reject_request", detail=request_id)
    mirror_state(db, sync)
    return updated


def accept_request(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    request_id: str,
    actor: str,
) -> TransferRequest:
    """Mark accepted and enqueue prepare on the target host (make-before-break)."""
    req = get_request(db, request_id)
    if req is None or req.status != "pending":
        raise HostControlError("Запрос уже закрыт.")
    entry = require_discord_capable(find_registry_host(sync, req.to_host))
    updated = _update_request(db, request_id, status="accepted")
    sync.write_command(
        req.to_host,
        HostCommand(
            action="prepare",
            request_id=req.id,
            issued_by=actor,
            payload={"target_host": req.to_host, "has_discord": entry.has_discord},
        ),
    )
    append_audit(
        db,
        actor=actor,
        action="accept_request",
        detail=f"{request_id} → prepare {req.to_host}",
    )
    mirror_state(db, sync)
    return updated


def issue_go_primary(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    host_id: str,
    request_id: str | None,
    actor: str,
) -> None:
    sync.write_command(
        host_id,
        HostCommand(
            action="go_primary",
            request_id=request_id,
            issued_by=actor,
            payload={},
        ),
    )
    append_audit(
        db,
        actor=actor,
        action="go_primary",
        detail=f"host={host_id} req={request_id or '-'}",
    )
    mirror_state(db, sync)


def issue_stop(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    host_id: str,
    actor: str,
    request_id: str | None = None,
) -> None:
    sync.write_command(
        host_id,
        HostCommand(
            action="stop",
            request_id=request_id,
            issued_by=actor,
            payload={},
        ),
    )
    append_audit(
        db, actor=actor, action="stop_command", detail=f"host={host_id}"
    )
    mirror_state(db, sync)


def issue_start(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    host_id: str,
    actor: str,
) -> None:
    entry = require_discord_capable(find_registry_host(sync, host_id))
    sync.write_command(
        host_id,
        HostCommand(
            action="start",
            issued_by=actor,
            payload={"has_discord": entry.has_discord},
        ),
    )
    append_audit(
        db, actor=actor, action="start_command", detail=f"host={host_id}"
    )
    mirror_state(db, sync)


def owner_force_to_host(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    target_host: str,
    actor: str,
    confirmed: bool,
) -> str:
    actor_id = actor.removeprefix("tg:").removeprefix("ds:")
    if not (is_owner_telegram(actor_id) or is_owner_discord(actor_id)):
        raise HostControlError("Force только для супер-админа.")
    if not confirmed:
        return "confirm_required"
    target = require_discord_capable(find_registry_host(sync, target_host))
    current = lease_status(db)
    if current.lease_active and current.primary_id and current.primary_id != target_host:
        owner_hosts = find_hosts_for_admin(
            sync, telegram_id=owner_telegram_id() or ""
        )
        owner_hosts += find_hosts_for_admin(
            sync, discord_id=owner_discord_id() or ""
        )
        moving_to_owner = target_host in {h.host_id for h in owner_hosts}
        owner_online = any(entry_is_online(h) for h in owner_hosts)
        if moving_to_owner and not owner_online:
            raise HostControlError(
                "ПК супер-админа офлайн — чужой primary не гасим."
            )
        issue_stop(
            db,
            sync,
            host_id=current.primary_id,
            actor=actor,
        )
    issue_start(db, sync, host_id=target.host_id, actor=actor)
    append_audit(
        db,
        actor=actor,
        action="force_transfer",
        detail=f"to={target_host}",
    )
    mirror_state(db, sync)
    return "ok"


def stop_local_and_failover_owner(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    actor: str,
    local_host: str | None = None,
) -> str:
    """Admin stops bot on own PC → try start on owner PC."""
    hid = local_host or resolve_host_id()
    issue_stop(db, sync, host_id=hid, actor=actor)
    owner_tg = owner_telegram_id()
    owner_hosts = [
        h
        for h in find_hosts_for_admin(sync, telegram_id=owner_tg or "")
        if entry_is_online(h) and h.has_discord
    ]
    if not owner_hosts:
        append_audit(
            db,
            actor=actor,
            action="stop_local_no_owner",
            detail=hid,
        )
        mirror_state(db, sync)
        return (
            "Бот на этом ПК будет остановлен. "
            "ПК супер-админа недоступен — primary может стать «не запущен»."
        )
    target = owner_hosts[0]
    issue_start(db, sync, host_id=target.host_id, actor=actor)
    append_audit(
        db,
        actor=actor,
        action="failover_owner",
        detail=f"from={hid} to={target.host_id}",
    )
    mirror_state(db, sync)
    return (
        f"Остановка {hid}. Запуск на ПК супер-админа: {target.host_id}."
    )


def process_ack(
    db: BridgeDatabase,
    sync: HostSyncStore,
    *,
    host_id: str,
    local_host: str | None = None,
) -> str | None:
    """React to agent ack. Returns action taken for logging."""
    ack = sync.read_ack(host_id)
    if ack is None:
        return None
    me = local_host or resolve_host_id()
    if ack.action == "prepare" and ack.ok:
        # Current primary releases and tells target to go_primary.
        if lease_status(db, me).is_primary:
            release(db, me)
            clear_primary_markers(db)
            issue_go_primary(
                db,
                sync,
                host_id=host_id,
                request_id=ack.request_id,
                actor="system:primary",
            )
            issue_stop(
                db,
                sync,
                host_id=me,
                actor="system:primary",
                request_id=ack.request_id,
            )
            sync.clear_ack(host_id)
            return "released_for_handover"
    if ack.action == "go_primary" and ack.ok:
        sync.clear_ack(host_id)
        return "target_primary_ok"
    if ack.action == "prepare" and not ack.ok:
        # Retry prepare
        sync.write_command(
            host_id,
            HostCommand(
                action="prepare",
                request_id=ack.request_id,
                issued_by="system:retry",
                payload={},
            ),
        )
        sync.clear_ack(host_id)
        return "retry_prepare"
    sync.clear_ack(host_id)
    return "ack_cleared"


def mirror_state(db: BridgeDatabase, sync: HostSyncStore | None = None) -> None:
    store = sync or HostSyncStore()
    info = lease_status(db)
    started = _parse_iso(db.get_setting(KEY_STARTED_AT))
    payload = {
        "primary_host": info.primary_id if info.lease_active else None,
        "lease_active": info.lease_active,
        "started_at": db.get_setting(KEY_STARTED_AT),
        "holder_admin": db.get_setting(KEY_HOLDER_ADMIN),
        "pending": [r.to_dict() for r in list_pending(db)],
        "audit_tail": _load_json_list(db, KEY_AUDIT)[-10:],
        "updated_at": _to_iso(),
        "status_label": (
            "primary"
            if info.lease_active and info.primary_id
            else "не запущен"
        ),
        "started_at_msk": _fmt_msk(started),
    }
    store.write_state(payload)


def panel_snapshot(
    db: BridgeDatabase,
    sync: HostSyncStore | None = None,
    *,
    host_id: str | None = None,
) -> HostPanelSnapshot:
    store = sync or HostSyncStore()
    hid = host_id or resolve_host_id()
    info = lease_status(db, hid)
    registry = store.list_registry()
    primary_entry = next(
        (e for e in registry if e.host_id == info.primary_id), None
    )
    started = _parse_iso(db.get_setting(KEY_STARTED_AT))
    has_discord = primary_entry.has_discord if primary_entry else None
    features = "не запущен"
    if info.lease_active and info.primary_id:
        features = "TG+Discord" if has_discord else "TG-only"
    label = (
        "primary"
        if info.lease_active and info.primary_id
        else "не запущен"
    )
    return HostPanelSnapshot(
        host_id=hid,
        primary_id=info.primary_id if info.lease_active else None,
        holder_admin=db.get_setting(KEY_HOLDER_ADMIN),
        started_at=started,
        lease_active=bool(info.lease_active and info.primary_id),
        is_primary=info.is_primary,
        os_name=primary_entry.os_name if primary_entry else None,
        has_discord=has_discord,
        features=features,
        pending=list_pending(db),
        registry=registry,
        status_label=label,
    )


async def control_loop(
    db: BridgeDatabase,
    sync: HostSyncStore | None = None,
    *,
    host_id: str | None = None,
    interval_sec: float = 2.0,
) -> None:
    """Primary-side loop: mirror state and process acks from all hosts."""
    import asyncio

    store = sync or HostSyncStore()
    hid = host_id or resolve_host_id()
    try:
        while True:
            mirror_state(db, store)
            for entry in store.list_registry():
                try:
                    process_ack(db, store, host_id=entry.host_id, local_host=hid)
                except Exception:  # noqa: BLE001
                    logger.exception("ack processing failed for %s", entry.host_id)
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        raise


# Re-exports for convenience
__all__ = [
    "INSTALL_HINT",
    "AuditEntry",
    "ConsentDeniedError",
    "EXIT_CONSENT_DENIED",
    "EXIT_LEASE_HELD",
    "HostControlError",
    "HostLeaseError",
    "HostLeaseStatus",
    "HostPanelSnapshot",
    "LeaseHeldError",
    "TransferRequest",
    "accept_request",
    "agent_online_sec",
    "append_audit",
    "cancel_request",
    "claim",
    "clear_primary_markers",
    "control_loop",
    "create_claim_request",
    "create_offer_request",
    "entry_is_online",
    "find_hosts_for_admin",
    "find_registry_host",
    "force_claim",
    "get_request",
    "grant_consent",
    "heartbeat_loop",
    "is_owner_discord",
    "is_owner_telegram",
    "is_primary",
    "issue_go_primary",
    "issue_start",
    "issue_stop",
    "list_audit",
    "list_pending",
    "mark_primary_started",
    "mirror_state",
    "owner_discord_id",
    "owner_force_to_host",
    "owner_telegram_id",
    "panel_snapshot",
    "process_ack",
    "reject_request",
    "release",
    "renew",
    "require_consent",
    "require_discord_capable",
    "resolve_host_id",
    "resolve_sync_dir",
    "revoke_consent",
    "stop_local_and_failover_owner",
    "lease_status",
]
