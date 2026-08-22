"""Unit tests for multi-PC host lease (settings-backed)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from bot.core.db import BridgeDatabase
from bot.core.host_lease import (
    ConsentDeniedError,
    EXIT_CONSENT_DENIED,
    EXIT_LEASE_HELD,
    KEY_LEASE_UNTIL,
    LeaseHeldError,
    claim,
    grant_consent,
    is_primary,
    release,
    renew,
    resolve_host_id,
    revoke_consent,
    status,
)
from bot.core.models import utcnow


@pytest.fixture()
def db(tmp_path) -> BridgeDatabase:
    return BridgeDatabase(str(tmp_path / "bridge.db"))


def test_resolve_host_id_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_ID", "desk-a")
    assert resolve_host_id() == "desk-a"


def test_claim_and_renew(db: BridgeDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_ID", "pc1")
    info = claim(db, "pc1")
    assert info.is_primary
    assert is_primary(db, "pc1")
    assert renew(db, "pc1") is True
    assert release(db, "pc1") is True
    assert not is_primary(db, "pc1")


def test_second_host_blocked_while_lease_alive(
    db: BridgeDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST_REQUIRE_CONSENT", raising=False)
    claim(db, "pc1")
    with pytest.raises(LeaseHeldError) as exc:
        claim(db, "pc2")
    assert exc.value.exit_code == EXIT_LEASE_HELD


def test_expired_lease_can_be_stolen(
    db: BridgeDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST_REQUIRE_CONSENT", raising=False)
    claim(db, "pc1")
    past = (utcnow() - timedelta(seconds=5)).isoformat()
    db.set_setting(KEY_LEASE_UNTIL, past)
    info = claim(db, "pc2")
    assert info.primary_id == "pc2"
    assert info.is_primary


def test_consent_locks_other_hosts(
    db: BridgeDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST_REQUIRE_CONSENT", raising=False)
    grant_consent(db, "pc1", admin_id="111")
    release(db, "pc1")
    with pytest.raises(ConsentDeniedError) as exc:
        claim(db, "pc2")
    assert exc.value.exit_code == EXIT_CONSENT_DENIED
    claim(db, "pc1")
    assert status(db, "pc1").consent_admin == "111"


def test_revoke_allows_other_host(
    db: BridgeDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST_REQUIRE_CONSENT", raising=False)
    grant_consent(db, "pc1", admin_id="111")
    revoke_consent(db, "pc1")
    info = claim(db, "pc2")
    assert info.primary_id == "pc2"


def test_require_consent_env(
    db: BridgeDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOST_REQUIRE_CONSENT", "1")
    with pytest.raises(ConsentDeniedError):
        claim(db, "pc1")
    grant_consent(db, "pc1", admin_id="42")
    assert is_primary(db, "pc1")
