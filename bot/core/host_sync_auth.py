"""HMAC signing and replay protection for host-sync JSON files."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REPLAY_WINDOW_SEC = 300
_NONCE_CACHE: dict[str, float] = {}


def resolve_sync_secret() -> str | None:
    raw = os.environ.get("HOST_SYNC_SECRET", "").strip()
    return raw or None


def require_sync_secret() -> str:
    secret = resolve_sync_secret()
    if not secret:
        raise RuntimeError(
            "HOST_SYNC_SECRET is required for host-sync commands/acks/registry. "
            "Set the same secret on every admin PC and the primary bot."
        )
    return secret


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(raw: str) -> float | None:
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _canonical_bytes(fields: dict[str, Any]) -> bytes:
    return json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_fields(fields: dict[str, Any], *, secret: str | None = None) -> dict[str, Any]:
    """Return ``fields`` plus ``ts``, ``nonce``, and ``sig``."""
    key = secret or require_sync_secret()
    out = dict(fields)
    out.setdefault("ts", _utc_ts())
    out.setdefault("nonce", secrets.token_hex(8))
    payload = {k: out[k] for k in sorted(out) if k not in ("sig",)}
    digest = hmac.new(key.encode("utf-8"), _canonical_bytes(payload), hashlib.sha256)
    out["sig"] = digest.hexdigest()
    return out


def _prune_nonce_cache(window_sec: float) -> None:
    now = time.time()
    stale = [n for n, exp in _NONCE_CACHE.items() if exp <= now]
    for nonce in stale:
        _NONCE_CACHE.pop(nonce, None)


def verify_signed_payload(
    data: dict[str, Any],
    *,
    secret: str | None = None,
    replay_window_sec: float = DEFAULT_REPLAY_WINDOW_SEC,
    check_replay: bool = True,
) -> bool:
    """Verify HMAC and optional replay window; record nonce on success."""
    key = secret or resolve_sync_secret()
    if not key:
        logger.error("host-sync: unsigned payload rejected (HOST_SYNC_SECRET unset)")
        return False
    sig = data.get("sig")
    if not isinstance(sig, str) or not sig:
        logger.warning("host-sync: missing signature")
        return False
    ts_raw = data.get("ts")
    nonce = data.get("nonce")
    if not isinstance(ts_raw, str) or not isinstance(nonce, str):
        logger.warning("host-sync: missing ts/nonce")
        return False
    ts = _parse_ts(ts_raw)
    if ts is None:
        logger.warning("host-sync: invalid ts")
        return False
    age = abs(time.time() - ts)
    if age > replay_window_sec:
        logger.warning("host-sync: stale payload (age=%.0fs)", age)
        return False
    payload = {k: data[k] for k in sorted(data) if k not in ("sig",)}
    expected = hmac.new(
        key.encode("utf-8"), _canonical_bytes(payload), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        logger.warning("host-sync: bad signature")
        return False
    if not check_replay:
        return True
    _prune_nonce_cache(replay_window_sec)
    if nonce in _NONCE_CACHE:
        logger.warning("host-sync: replay nonce %s", nonce[:8])
        return False
    _NONCE_CACHE[nonce] = time.time() + replay_window_sec
    return True


def strip_auth_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in ("sig", "ts", "nonce")}
