from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `import bot.core...` when pytest is started from the repo root.
PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


@pytest.fixture(autouse=True)
def host_sync_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """All host-sync tests use a shared HMAC secret."""
    monkeypatch.setenv("HOST_SYNC_SECRET", "test-sync-secret-for-pytest")
