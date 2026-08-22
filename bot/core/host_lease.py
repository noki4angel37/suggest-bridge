"""Multi-PC primary lease for Telegram polling (one getUpdates host at a time).

Settings keys in bridge.db (`settings` table):
  host_primary_id    — HOST_ID that currently holds the lease
  host_consent_admin — Telegram admin id who ran /host_consent (empty = unlocked)
  host_consent_host  — HOST_ID allowed to claim while consent is set
  host_heartbeat_at  — ISO UTC of last heartbeat
  host_lease_until   — ISO UTC when the lease expires

HOST_ID comes from env HOST_ID, else ``{hostname}:{username}``.
PowerShell install scripts set HOST_ID for multi-PC host coordination.
Do not print secrets. Do not commit `.env` or `local.env`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bot.core.db import BridgeDatabase

logger = logging.getLogger(__name__)

KEY_PRIMARY = "host_primary_id"
KEY_CONSENT_ADMIN = "host_consent_admin"
KEY_CONSENT_HOST = "host_consent_host"
KEY_HEARTBEAT = "host_heartbeat_at"
KEY_LEASE_UNTIL = "host_lease_until"

DEFAULT_LEASE_TTL_SEC = 60
DEFAULT_HEARTBEAT_SEC = 20

# Process exit codes for run-telegram-suggest-bot.ps1 (hold/release policy).
EXIT_LEASE_HELD = 2
EXIT_CONSENT_DENIED = 3


class HostLeaseError(Exception):
    """Base lease failure; ``message`` is safe to log (Russian, no secrets)."""

    exit_code: int = 1

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class LeaseHeldError(HostLeaseError):
    exit_code = EXIT_LEASE_HELD


class ConsentDeniedError(HostLeaseError):
    exit_code = EXIT_CONSENT_DENIED


@dataclass(frozen=True)
class HostLeaseStatus:
    host_id: str
    primary_id: str | None
    consent_admin: str | None
    consent_host: str | None
    heartbeat_at: datetime | None
    lease_until: datetime | None
    is_primary: bool
    lease_active: bool


def resolve_host_id() -> str:
    """HOST_ID from env, else ``{hostname}:{username}``."""
    raw = os.environ.get("HOST_ID", "").strip()
    if raw:
        return raw
    host = socket.gethostname().strip() or "unknown-host"
    user = (
        os.environ.get("USERNAME")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or "user"
    ).strip()
    return f"{host}:{user}"


def write_lease(db: BridgeDatabase, host_id: str, *, now: datetime | None = None) -> None:
    """Public lease writer (claim / force / renew)."""
    _write_lease(db, host_id, now=now)


def lease_ttl_sec() -> int:
    return _env_positive_int("HOST_LEASE_TTL_SEC", DEFAULT_LEASE_TTL_SEC)


def heartbeat_interval_sec() -> float:
    return float(_env_positive_int("HOST_HEARTBEAT_SEC", DEFAULT_HEARTBEAT_SEC))


def require_consent() -> bool:
    """If true, claim needs /host_consent even on a fresh DB."""
    raw = os.environ.get("HOST_REQUIRE_CONSENT", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(moment: datetime) -> str:
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


def _clear(db: BridgeDatabase, *keys: str) -> None:
    for key in keys:
        db.delete_setting(key)


def status(db: BridgeDatabase, host_id: str | None = None) -> HostLeaseStatus:
    hid = host_id or resolve_host_id()
    primary = db.get_setting(KEY_PRIMARY)
    until = _parse_iso(db.get_setting(KEY_LEASE_UNTIL))
    now = _utcnow()
    active = until is not None and until > now and bool(primary)
    return HostLeaseStatus(
        host_id=hid,
        primary_id=primary,
        consent_admin=db.get_setting(KEY_CONSENT_ADMIN),
        consent_host=db.get_setting(KEY_CONSENT_HOST),
        heartbeat_at=_parse_iso(db.get_setting(KEY_HEARTBEAT)),
        lease_until=until,
        is_primary=bool(primary) and primary == hid and active,
        lease_active=active,
    )


def is_primary(db: BridgeDatabase, host_id: str | None = None) -> bool:
    return status(db, host_id).is_primary


def _consent_allows(db: BridgeDatabase, host_id: str) -> bool:
    consent_host = db.get_setting(KEY_CONSENT_HOST)
    consent_admin = db.get_setting(KEY_CONSENT_ADMIN)
    if not consent_host and not consent_admin:
        return not require_consent()
    if consent_host and consent_host != host_id:
        return False
    return True


def _write_lease(db: BridgeDatabase, host_id: str, *, now: datetime | None = None) -> None:
    moment = now or _utcnow()
    until = moment + timedelta(seconds=lease_ttl_sec())
    db.set_setting(KEY_PRIMARY, host_id)
    db.set_setting(KEY_HEARTBEAT, _to_iso(moment))
    db.set_setting(KEY_LEASE_UNTIL, _to_iso(until))


def claim(db: BridgeDatabase, host_id: str | None = None) -> HostLeaseStatus:
    """Claim the primary lease or raise HostLeaseError (do not start polling)."""
    hid = host_id or resolve_host_id()
    if not _consent_allows(db, hid):
        consented = db.get_setting(KEY_CONSENT_HOST)
        if consented:
            detail = f"согласие выдано для {consented}"
        else:
            detail = "нужно /host_consent (HOST_REQUIRE_CONSENT=1)"
        raise ConsentDeniedError(
            "Нет согласия админа на роль primary для этого хоста "
            f"(HOST_ID={hid}, {detail}). "
            "На активном боте выполните /host_release, затем на этом хосте "
            "после старта — /host_consent."
        )

    current = status(db, hid)
    if current.lease_active and current.primary_id and current.primary_id != hid:
        raise LeaseHeldError(
            "Лицензия primary уже занята другим хостом "
            f"({current.primary_id}) до {current.lease_until}. "
            f"Этот HOST_ID={hid} не будет запускать getUpdates "
            "(защита от TelegramConflictError)."
        )

    _write_lease(db, hid)
    logger.info("Host lease claimed: HOST_ID=%s ttl=%ss", hid, lease_ttl_sec())
    return status(db, hid)


def renew(db: BridgeDatabase, host_id: str | None = None) -> bool:
    """Extend lease if we are still recorded as primary; return False if lost."""
    hid = host_id or resolve_host_id()
    if db.get_setting(KEY_PRIMARY) != hid:
        return False
    _write_lease(db, hid)
    return True


def release(db: BridgeDatabase, host_id: str | None = None) -> bool:
    """Release lease if we hold it. Consent is left unchanged."""
    hid = host_id or resolve_host_id()
    primary = db.get_setting(KEY_PRIMARY)
    if primary is not None and primary != hid:
        return False
    _clear(db, KEY_PRIMARY, KEY_HEARTBEAT, KEY_LEASE_UNTIL)
    logger.info("Host lease released: HOST_ID=%s", hid)
    return True


def grant_consent(
    db: BridgeDatabase,
    host_id: str | None = None,
    *,
    admin_id: str | int,
) -> HostLeaseStatus:
    """Record admin consent for this machine and claim/renew the lease."""
    hid = host_id or resolve_host_id()
    db.set_setting(KEY_CONSENT_HOST, hid)
    db.set_setting(KEY_CONSENT_ADMIN, str(admin_id))
    current = status(db, hid)
    if current.lease_active and current.primary_id and current.primary_id != hid:
        raise LeaseHeldError(
            "Согласие записано, но лизинг primary сейчас у "
            f"{current.primary_id}. Дождитесь истечения или /host_release там."
        )
    _write_lease(db, hid)
    logger.info(
        "Host consent granted: HOST_ID=%s admin=%s", hid, admin_id
    )
    return status(db, hid)


def revoke_consent(db: BridgeDatabase, host_id: str | None = None) -> None:
    """Clear consent and release lease (allows another host to claim)."""
    hid = host_id or resolve_host_id()
    _clear(
        db,
        KEY_PRIMARY,
        KEY_HEARTBEAT,
        KEY_LEASE_UNTIL,
        KEY_CONSENT_ADMIN,
        KEY_CONSENT_HOST,
    )
    logger.info("Host consent revoked and lease cleared: HOST_ID=%s", hid)


async def heartbeat_loop(
    db: BridgeDatabase,
    host_id: str | None = None,
    *,
    interval_sec: float | None = None,
) -> None:
    """Renew lease until cancelled. Stops quietly if lease is lost."""
    hid = host_id or resolve_host_id()
    delay = heartbeat_interval_sec() if interval_sec is None else interval_sec
    try:
        while True:
            await asyncio.sleep(delay)
            if not renew(db, hid):
                logger.error(
                    "Host lease lost for HOST_ID=%s — остановите поллинг вручную",
                    hid,
                )
                return
    except asyncio.CancelledError:
        raise
