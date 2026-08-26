from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.core.db import BridgeDatabase
from bot.core.models import PassRequestStatus
from bot.core.pass_config import (
    PassConfig,
    pass_role_setting_key,
    resolve_pass_role_id,
)
from bot.core.pass_service import PassService, format_remaining


def _cfg(**kwargs: object) -> PassConfig:
    defaults: dict[str, object] = {
        "role_id": "100",
        "duration_sec": 5 * 60 * 60,
        "reject_cooldown_sec": 5 * 60,
        "antispam_limit": 3,
        "antispam_window_sec": 600,
        "antispam_strike_sec": 900,
        "debounce_sec": 8,
    }
    defaults.update(kwargs)
    return PassConfig(**defaults)  # type: ignore[arg-type]


def _svc(tmp_path: Path, now: datetime, **kwargs: object) -> PassService:
    clock = now.timestamp()
    return PassService(
        BridgeDatabase(str(tmp_path / "bridge.db")),
        _cfg(**kwargs),
        now=lambda: now,
        clock=lambda: clock,
    )


def _create(svc: PassService, user: str = "105", **kwargs: object):
    defaults: dict[str, object] = {
        "guild_id": "200",
        "user_id": user,
        "display_name": "member",
        "username": "member",
        "blocked": False,
    }
    defaults.update(kwargs)
    return svc.create_request(**defaults)  # type: ignore[arg-type]


def test_format_remaining() -> None:
    assert format_remaining(9) == "9 с"
    assert format_remaining(300) == "5 мин"
    assert format_remaining(18000) == "5 ч"


def test_create_then_approve_sets_expiry(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now)
    created = _create(svc)
    assert created.ok is True
    assert created.request is not None
    decided = svc.approve(int(created.request.id), decided_by="admin")
    assert decided.ok is True
    assert decided.request is not None
    assert decided.request.status is PassRequestStatus.approved
    assert decided.request.expires_at == now + timedelta(hours=5)


def test_second_pending_is_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now, debounce_sec=0)
    assert _create(svc).ok is True
    again = _create(svc)
    assert again.ok is False
    assert again.reason == "pending"


def test_reject_applies_five_minute_cooldown(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now, debounce_sec=0)
    created = _create(svc)
    assert created.request is not None
    rejected = svc.reject(int(created.request.id), decided_by="admin")
    assert rejected.ok is True
    blocked = _create(svc)
    assert blocked.ok is False
    assert blocked.reason == "cooldown"
    assert blocked.retry_after_sec == 300


def test_reject_cooldown_expires(tmp_path: Path) -> None:
    start = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    db = BridgeDatabase(str(tmp_path / "bridge.db"))
    current = {"t": start}

    def now() -> datetime:
        return current["t"]

    svc = PassService(
        db,
        _cfg(debounce_sec=0),
        now=now,
        clock=lambda: current["t"].timestamp(),
    )
    created = _create(svc)
    assert created.request is not None
    svc.reject(int(created.request.id), decided_by="admin")
    current["t"] = start + timedelta(minutes=5, seconds=1)
    again = _create(svc)
    assert again.ok is True


def test_active_grant_blocks_new_request(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now, debounce_sec=0)
    created = _create(svc)
    assert created.request is not None
    svc.approve(int(created.request.id), decided_by="admin")
    again = _create(svc)
    assert again.ok is False
    assert again.reason == "active"


def test_already_has_role_blocks(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now)
    result = _create(svc, already_has_role=True)
    assert result.ok is False
    assert result.reason == "has_role"


def test_blocked_user_cannot_request(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now)
    result = _create(svc, blocked=True)
    assert result.ok is False
    assert result.reason == "blocked"


def test_disabled_without_role_id(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now, role_id=None)
    result = _create(svc)
    assert result.ok is False
    assert result.reason == "disabled"


def test_guild_setting_enables_without_env_role(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    db = BridgeDatabase(str(tmp_path / "bridge.db"))
    db.set_setting(pass_role_setting_key("200"), "555")
    svc = PassService(
        db,
        _cfg(role_id=None, debounce_sec=0),
        now=lambda: now,
        clock=lambda: now.timestamp(),
    )
    created = _create(svc)
    assert created.ok is True
    assert svc.role_id_for("200") == "555"


def test_guild_setting_wins_over_env_role(tmp_path: Path) -> None:
    db = BridgeDatabase(str(tmp_path / "bridge.db"))
    db.set_setting(pass_role_setting_key("200"), "555")
    config = _cfg(role_id="100")
    assert resolve_pass_role_id(db, "200", config) == "555"
    assert resolve_pass_role_id(db, "999", config) == "100"


def test_double_approve_is_handled(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now)
    created = _create(svc)
    assert created.request is not None
    first = svc.approve(int(created.request.id), decided_by="a")
    second = svc.approve(int(created.request.id), decided_by="b")
    assert first.ok is True
    assert second.ok is False
    assert second.reason == "handled"


def test_antispam_strike_after_burst(tmp_path: Path) -> None:
    start = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    db = BridgeDatabase(str(tmp_path / "bridge.db"))
    current = {"t": start}

    svc = PassService(
        db,
        _cfg(antispam_limit=2, debounce_sec=0, antispam_strike_sec=900),
        now=lambda: current["t"],
        clock=lambda: current["t"].timestamp(),
    )
    assert _create(svc, user="1").ok is True
    current["t"] = start + timedelta(seconds=1)
    pending = _create(svc, user="1")
    assert pending.reason == "pending"
    current["t"] = start + timedelta(seconds=2)
    struck = _create(svc, user="1")
    assert struck.ok is False
    assert struck.reason == "antispam"


def test_debounce_blocks_rapid_retry(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now, debounce_sec=8)
    first = _create(svc)
    assert first.ok is True
    second = _create(svc)
    assert second.ok is False
    assert second.reason == "debounce"


def test_abort_allows_immediate_retry(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now, debounce_sec=0)
    created = _create(svc)
    assert created.request is not None
    aborted = svc.abort(int(created.request.id))
    assert aborted is not None
    assert aborted.status is PassRequestStatus.rejected
    again = _create(svc)
    assert again.ok is True


def test_reopen_after_failed_grant_allows_retry(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now, debounce_sec=0)
    created = _create(svc)
    assert created.request is not None
    approved = svc.approve(int(created.request.id), decided_by="admin")
    assert approved.request is not None
    reopened = svc.reopen(int(approved.request.id))
    assert reopened is not None
    assert reopened.status is PassRequestStatus.pending
    again = _create(svc)
    assert again.ok is False
    assert again.reason == "pending"
    decided = svc.approve(int(reopened.id), decided_by="admin")
    assert decided.ok is True


def test_expire_keeps_expires_at(tmp_path: Path) -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)
    svc = _svc(tmp_path, now)
    created = _create(svc)
    assert created.request is not None
    approved = svc.approve(int(created.request.id), decided_by="admin")
    assert approved.request is not None
    expired = svc.expire(int(approved.request.id))
    assert expired is not None
    assert expired.status is PassRequestStatus.expired
    assert expired.expires_at == approved.request.expires_at
