from __future__ import annotations

from dataclasses import dataclass, field

from bot.adapters.discord import permissions
from bot.core.models import GuildConfig


@dataclass
class FakeRole:
    id: int


@dataclass
class FakePerms:
    administrator: bool = False
    manage_guild: bool = False


@dataclass
class FakeMember:
    id: int = 1
    roles: list[FakeRole] = field(default_factory=list)
    guild_permissions: FakePerms = field(default_factory=FakePerms)


def config(**kwargs: object) -> GuildConfig:
    return GuildConfig(guild_id="900", **kwargs)  # type: ignore[arg-type]


def test_normalize_ids_accepts_objects_and_numbers() -> None:
    assert permissions.normalize_ids([FakeRole(10), 11, "12", None]) == [
        "10",
        "11",
        "12",
    ]
    assert permissions.normalize_ids(None) == []


def test_has_any_role_needs_non_empty_allowlist() -> None:
    assert permissions.has_any_role(["1"], []) is False
    assert permissions.has_any_role(["1", "2"], ["2"]) is True
    assert permissions.has_any_role(["1"], ["2"]) is False


def test_can_propose_defaults_to_everyone() -> None:
    assert permissions.can_propose([], []) is True
    assert permissions.can_propose(["9"], ["10"]) is False
    assert permissions.can_propose(["10"], ["10"]) is True
    assert permissions.can_propose([], ["10"], is_platform_admin=True) is True
    assert permissions.can_propose([], ["10"], is_guild_admin=True) is True


def test_can_moderate_requires_role_or_admin() -> None:
    assert permissions.can_moderate([], []) is False
    assert permissions.can_moderate(["20"], ["20"]) is True
    assert permissions.can_moderate(["21"], ["20"]) is False
    assert permissions.can_moderate([], [], is_platform_admin=True) is True
    assert permissions.can_moderate([], [], is_guild_admin=True) is True


def test_can_setup_only_for_admins() -> None:
    assert permissions.can_setup() is False
    assert permissions.can_setup(is_guild_admin=True) is True
    assert permissions.can_setup(is_platform_admin=True) is True


def test_member_helpers_read_roles_and_permissions() -> None:
    member = FakeMember(id=5, roles=[FakeRole(10), FakeRole(20)])
    assert permissions.member_role_ids(member) == ["10", "20"]
    assert permissions.is_guild_admin(member) is False
    assert permissions.is_guild_admin(
        FakeMember(guild_permissions=FakePerms(administrator=True))
    ) is True


def test_member_can_propose_uses_guild_config() -> None:
    member = FakeMember(id=5, roles=[FakeRole(10)])
    assert (
        permissions.member_can_propose(member, config(propose_role_ids=["10"]))
        is True
    )
    assert (
        permissions.member_can_propose(member, config(propose_role_ids=["77"]))
        is False
    )
    # No config for the guild yet: submitting stays open.
    assert permissions.member_can_propose(member, None) is True


def test_member_can_moderate_uses_guild_config_and_admins() -> None:
    member = FakeMember(id=5, roles=[FakeRole(20)])
    assert (
        permissions.member_can_moderate(member, config(mod_role_ids=["20"]))
        is True
    )
    assert (
        permissions.member_can_moderate(member, config(mod_role_ids=["21"]))
        is False
    )
    assert permissions.member_can_moderate(member, None) is False
    assert (
        permissions.member_can_moderate(
            member, None, is_platform_admin=True
        )
        is True
    )
    assert (
        permissions.member_can_moderate(
            FakeMember(guild_permissions=FakePerms(manage_guild=True)), None
        )
        is True
    )
