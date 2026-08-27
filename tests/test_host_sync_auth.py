"""Tests for host-sync HMAC signing and verification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.core.host_sync import HostCommand, HostRegistryEntry, HostSyncStore
from bot.core.host_sync_auth import sign_fields, verify_signed_payload


@pytest.fixture()
def sync(tmp_path: Path) -> HostSyncStore:
    return HostSyncStore(tmp_path / "sync")


def test_command_roundtrip_signed(sync: HostSyncStore) -> None:
    cmd = HostCommand(action="stop", request_id="abc", issued_by="tg:1")
    sync.write_command("pc-a:u", cmd)
    raw = json.loads(sync.command_path("pc-a:u").read_text(encoding="utf-8"))
    assert "sig" in raw
    loaded = sync.read_command("pc-a:u")
    assert loaded is not None
    assert loaded.action == "stop"


def test_unsigned_command_rejected(sync: HostSyncStore) -> None:
    sync.command_path("pc-b:u").parent.mkdir(parents=True, exist_ok=True)
    sync.command_path("pc-b:u").write_text(
        json.dumps({"action": "stop"}), encoding="utf-8"
    )
    assert sync.read_command("pc-b:u") is None


def test_registry_signed(sync: HostSyncStore) -> None:
    entry = HostRegistryEntry(host_id="z:u", admin_telegram_id="111")
    sync.write_registry(entry)
    assert sync.read_registry("z:u") is not None


def test_replay_nonce_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_SYNC_SECRET", "s")
    payload = sign_fields({"action": "stop"}, secret="s")
    assert verify_signed_payload(payload, secret="s")
    assert not verify_signed_payload(payload, secret="s")
