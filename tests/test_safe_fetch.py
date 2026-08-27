"""Tests for safe_fetch URL allowlist."""

from __future__ import annotations

import pytest

from bot.core.safe_fetch import UnsafeUrlError, validate_fetch_url


def test_allows_discord_cdn() -> None:
    validate_fetch_url("https://cdn.discordapp.com/attachments/1/2/x.png")


def test_rejects_unknown_host() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_fetch_url("http://127.0.0.1:8080/secret")


def test_rejects_private_metadata() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_fetch_url("http://169.254.169.254/latest/meta-data/")
