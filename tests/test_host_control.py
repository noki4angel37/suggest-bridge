"""Tests for host_control + host_sync transfer plane."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from bot.core.db import BridgeDatabase
from bot.core.host_control import (
    HostControlError,
    accept_request,
    create_claim_request,
    create_offer_request,
    entry_is_online,
    force_claim,
    is_owner_discord,
    is_owner_telegram,
    list_pending,
    mark_primary_started,
    owner_force_to_host,
    panel_snapshot,
    reject_request,
    require_discord_capable,
    resolve_actor_host,
    stop_local_and_failover_owner,
)
from bot.core.host_lease import claim, resolve_host_id
from bot.core.host_sync import (
    HostRegistryEntry,
    HostSyncStore,
)
from bot.core.models import utcnow


@pytest.fixture()
def db(tmp_path: Path) -> BridgeDatabase:
    return BridgeDatabase(str(tmp_path / "bridge.db"))


@pytest.fixture()
def sync(tmp_path: Path) -> HostSyncStore:
    return HostSyncStore(tmp_path / "suggest-host-sync")


def _online_entry(
    host_id: str,
    *,
    tg: str = "111",
    has_discord: bool = True,
) -> HostRegistryEntry:
    return HostRegistryEntry(
        host_id=host_id,
        admin_telegram_id=tg,
        admin_discord_id="222",
        has_discord=has_discord,
        agent_online=True,
        last_seen=utcnow().isoformat(),
        os_name="Windows",
    )


def test_resolve_host_id_hostname_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST_ID", raising=False)
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setattr("bot.core.host_lease.socket.gethostname", lambda: "DESK")
    assert resolve_host_id() == "DESK:alice"


def test_owner_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "111")
    monkeypatch.setenv("OWNER_DISCORD_ID", "222")
    assert is_owner_telegram("111")
    assert is_owner_discord("222")
    assert not is_owner_telegram("1")


def test_owner_telegram_falls_back_to_first_admin_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OWNER_TELEGRAM_ID", raising=False)
    monkeypatch.setenv("ADMIN_IDS", "333,444")
    assert is_owner_telegram("333")
    assert not is_owner_telegram("444")


def test_claim_request_requires_online_discord(
    db: BridgeDatabase, sync: HostSyncStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST_REQUIRE_CONSENT", raising=False)
    with pytest.raises(HostControlError):
        create_claim_request(db, sync, admin_id="111", target_host="missing")
    sync.write_registry(
        _online_entry("pc-a:u", tg="111", has_discord=False)
    )
    with pytest.raises(HostControlError):
        create_claim_request(db, sync, admin_id="111", target_host="pc-a:u")
    sync.write_registry(_online_entry("pc-a:u", tg="111", has_discord=True))
    req = create_claim_request(db, sync, admin_id="111", target_host="pc-a:u")
    assert req.kind == "claim"
    assert list_pending(db)


def test_accept_writes_prepare_command(
    db: BridgeDatabase, sync: HostSyncStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST_REQUIRE_CONSENT", raising=False)
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "111")
    claim(db, "pc-old:u")
    mark_primary_started(db, "pc-old:u", holder_admin="111")
    sync.write_registry(_online_entry("pc-new:u", tg="222"))
    req = create_claim_request(db, sync, admin_id="222", target_host="pc-new:u")
    accept_request(db, sync, request_id=req.id, actor="tg:111")
    cmd = sync.read_command("pc-new:u")
    assert cmd is not None
    assert cmd.action == "prepare"


def test_accept_reject_unauthorized(
    db: BridgeDatabase, sync: HostSyncStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST_REQUIRE_CONSENT", raising=False)
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "999")
    claim(db, "pc-old:u")
    mark_primary_started(db, "pc-old:u", holder_admin="111")
    sync.write_registry(_online_entry("pc-new:u", tg="222"))
    req = create_claim_request(db, sync, admin_id="222", target_host="pc-new:u")
    with pytest.raises(HostControlError, match="держатель"):
        accept_request(db, sync, request_id=req.id, actor="tg:222")
    with pytest.raises(HostControlError, match="держатель"):
        reject_request(db, sync, request_id=req.id, actor="tg:222")
    accept_request(db, sync, request_id=req.id, actor="tg:999")


def test_stop_local_uses_actor_registry_not_primary(
    db: BridgeDatabase, sync: HostSyncStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "111")
    claim(db, "primary-pc:u")
    sync.write_registry(_online_entry("admin-pc:u", tg="222"))
    sync.write_registry(_online_entry("owner-pc:u", tg="111"))
    hid = resolve_actor_host(sync, actor="tg:222")
    assert hid == "admin-pc:u"
    msg = stop_local_and_failover_owner(
        db, sync, actor="tg:222", local_host=hid
    )
    assert "admin-pc:u" in msg
    cmd = sync.read_command("admin-pc:u")
    assert cmd is not None and cmd.action == "stop"


def test_reject_and_cooldown(
    db: BridgeDatabase, sync: HostSyncStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOST_REQUEST_COOLDOWN_SEC", "60")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "111")
    claim(db, "pc-old:u")
    mark_primary_started(db, "pc-old:u", holder_admin="111")
    sync.write_registry(_online_entry("pc-b:u", tg="333"))
    req = create_claim_request(db, sync, admin_id="333", target_host="pc-b:u")
    reject_request(db, sync, request_id=req.id, actor="tg:111")
    with pytest.raises(HostControlError, match="частые"):
        create_claim_request(db, sync, admin_id="333", target_host="pc-b:u")


def test_force_claim_steals_lease(
    db: BridgeDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST_REQUIRE_CONSENT", raising=False)
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "111")
    claim(db, "pc1")
    info = force_claim(db, "pc2", holder_admin="111")
    assert info.primary_id == "pc2"
    assert info.is_primary


def test_owner_force_requires_confirm(
    db: BridgeDatabase, sync: HostSyncStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "111")
    sync.write_registry(_online_entry("owner-pc:u"))
    assert (
        owner_force_to_host(
            db,
            sync,
            target_host="owner-pc:u",
            actor="tg:111",
            confirmed=False,
        )
        == "confirm_required"
    )
    assert (
        owner_force_to_host(
            db,
            sync,
            target_host="owner-pc:u",
            actor="tg:111",
            confirmed=True,
        )
        == "ok"
    )
    cmd = sync.read_command("owner-pc:u")
    assert cmd is not None and cmd.action == "start"


def test_panel_not_running(db: BridgeDatabase, sync: HostSyncStore) -> None:
    snap = panel_snapshot(db, sync, host_id="x:y")
    assert snap.status_label == "не запущен"
    assert "не запущен" in snap.format_msk()


def test_entry_online_window(sync: HostSyncStore) -> None:
    entry = _online_entry("z:u")
    sync.write_registry(entry)
    loaded = sync.read_registry("z:u")
    assert entry_is_online(loaded)
    stale = _online_entry("z:u")
    stale.last_seen = (utcnow() - timedelta(hours=1)).isoformat()
    assert not entry_is_online(stale)
    with pytest.raises(HostControlError):
        require_discord_capable(stale)


def test_offer_request(
    db: BridgeDatabase, sync: HostSyncStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOST_REQUEST_COOLDOWN_SEC", "1")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "111")
    sync.write_registry(_online_entry("pc-c:u", tg="444"))
    req = create_offer_request(
        db, sync, from_admin="111", to_host="pc-c:u"
    )
    assert req.kind == "offer"
    assert req.to_host == "pc-c:u"
