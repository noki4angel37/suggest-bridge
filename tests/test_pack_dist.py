"""Tests for standalone suggest-bot zip packing."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from bot.core.pack_dist import (
    EXCLUDE_FILE_NAMES,
    REQUIRED_RELATIVE,
    build_suggest_bot_zip,
    package_root,
)


def test_package_root_is_bot_parent() -> None:
    root = package_root()
    assert (root / "bot" / "main.py").is_file()
    assert (root / "install-agent.ps1").is_file()


def test_build_suggest_bot_zip_excludes_secrets(tmp_path: Path) -> None:
    root = package_root()
    # Ensure a decoy secret in a temp overlay is not needed — pack from real
    # package and assert local.env is never inside the archive.
    zip_path = build_suggest_bot_zip(out_dir=tmp_path, root=root)
    assert zip_path.is_file()
    assert zip_path.stat().st_size > 1000

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
    for rel in REQUIRED_RELATIVE:
        assert rel in names, rel
    for secret in EXCLUDE_FILE_NAMES:
        assert secret not in names
        assert f"data/{secret}" not in names
    assert not any(n.startswith("data/") for n in names)
    assert not any(".venv/" in n for n in names)
    assert not any("__pycache__/" in n for n in names)
    assert not any(n.endswith(".pyc") for n in names)


def test_build_rejects_incomplete_tree(tmp_path: Path) -> None:
    incomplete = tmp_path / "pkg"
    incomplete.mkdir()
    (incomplete / "bot").mkdir()
    with pytest.raises(FileNotFoundError, match="missing"):
        build_suggest_bot_zip(out_dir=tmp_path / "out", root=incomplete)
