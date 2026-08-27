"""Unit tests for visible pass-room message guard (no Discord API)."""

from __future__ import annotations

from bot.adapters.discord.pass_guard import (
    is_pass_guard_room,
    should_delete_pass_message,
)
from bot.core.pass_rooms_store import (
    PASS_KIND_TEXT,
    PASS_KIND_VOICE,
    PASS_MODE_HIDE,
    PASS_MODE_VISIBLE,
    PassRoomEntry,
)


def test_is_pass_guard_room_only_visible_text() -> None:
    assert is_pass_guard_room(None) is False
    assert (
        is_pass_guard_room(
            PassRoomEntry("1", PASS_MODE_VISIBLE, PASS_KIND_TEXT)
        )
        is True
    )
    assert (
        is_pass_guard_room(
            PassRoomEntry("1", PASS_MODE_HIDE, PASS_KIND_TEXT)
        )
        is False
    )
    assert (
        is_pass_guard_room(
            PassRoomEntry("1", PASS_MODE_VISIBLE, PASS_KIND_VOICE)
        )
        is False
    )


def test_should_delete_pass_message_exempts_self_pass_owner_admin() -> None:
    assert should_delete_pass_message(is_self_bot=True) is False
    assert should_delete_pass_message(has_pass_role=True) is False
    assert should_delete_pass_message(is_guild_owner=True) is False
    assert should_delete_pass_message(is_bot_admin=True) is False


def test_should_delete_pass_message_targets_outsiders() -> None:
    # Regular member without pass, other bots, webhooks: all flags false.
    assert should_delete_pass_message() is True
    assert (
        should_delete_pass_message(
            is_self_bot=False,
            has_pass_role=False,
            is_guild_owner=False,
            is_bot_admin=False,
        )
        is True
    )


def test_guard_lookup_channel_id_thread_uses_parent() -> None:
    from bot.adapters.discord.pass_guard import guard_lookup_channel_id

    assert (
        guard_lookup_channel_id(
            is_thread=False, channel_id=10, parent_id=None
        )
        == 10
    )
    assert (
        guard_lookup_channel_id(
            is_thread=True, channel_id=99, parent_id=10
        )
        == 10
    )
    assert (
        guard_lookup_channel_id(
            is_thread=True, channel_id=99, parent_id=None
        )
        is None
    )


def test_should_delete_pass_message_admin_wins_over_no_pass() -> None:
    assert (
        should_delete_pass_message(
            has_pass_role=False, is_bot_admin=True
        )
        is False
    )
