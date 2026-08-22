"""Tests for TG↔Discord mirror text formatting and edit guards."""

from __future__ import annotations

from types import SimpleNamespace

from bot.adapters.discord.mirror import (
    DS_PREFIX,
    TG_PREFIX,
    format_discord_mirror_text,
    format_telegram_mirror_text,
)


def test_format_discord_mirror_text_mark_only_no_links() -> None:
    message = SimpleNamespace(text="говнооо", caption=None)
    text = format_discord_mirror_text(message)  # type: ignore[arg-type]
    assert text == f"{DS_PREFIX}\nговнооо"
    assert "https://" not in text
    assert "t.me" not in text


def test_format_discord_mirror_strips_legacy_tg_permalink() -> None:
    message = SimpleNamespace(
        text="когда\nhttps://t.me/c/2342483038/4116",
        caption=None,
    )
    text = format_discord_mirror_text(message)  # type: ignore[arg-type]
    assert text == f"{DS_PREFIX}\nкогда"
    assert "t.me" not in text


def test_format_discord_mirror_text_uses_caption() -> None:
    message = SimpleNamespace(text=None, caption="фото")
    assert format_discord_mirror_text(message) == f"{DS_PREFIX}\nфото"  # type: ignore[arg-type]


def test_format_telegram_mirror_text_mark_only_no_jump_url() -> None:
    text = format_telegram_mirror_text("привет")
    assert text == f"{TG_PREFIX}\nпривет"
    assert "discord.com" not in text
    assert "t.me" not in text


def test_format_telegram_mirror_strips_old_cross_links_and_prefixes() -> None:
    raw = "\n".join(
        [
            DS_PREFIX,
            "говнооо",
            "https://t.me/c/2342483038/4115",
            "https://discord.com/channels/1/2/3",
        ]
    )
    text = format_telegram_mirror_text(raw)
    assert text == f"{TG_PREFIX}\nговнооо"
    assert "https://" not in text


def test_format_telegram_keeps_inline_user_urls_in_body() -> None:
    # Only standalone cross-post link lines are dropped, not body URLs mid-sentence.
    text = format_telegram_mirror_text("смотри https://example.com/page")
    assert "https://example.com/page" in text
    assert text.startswith(TG_PREFIX)
