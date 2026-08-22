"""Role checks for the Discord adapter.

Pure functions on id lists plus duck-typed helpers over discord.py members,
so the module imports no discord.py and stays unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from bot.core.models import GuildConfig


def normalize_ids(values: Iterable[Any] | None) -> list[str]:
    """Ids come from JSON, mentions and discord.py objects: unify to strings."""
    result: list[str] = []
    for value in values or ():
        if value is None:
            continue
        raw = getattr(value, "id", value)
        text = str(raw).strip()
        if text:
            result.append(text)
    return result


def has_any_role(
    role_ids: Iterable[Any] | None, allowed_ids: Iterable[Any] | None
) -> bool:
    allowed = set(normalize_ids(allowed_ids))
    if not allowed:
        return False
    return any(role in allowed for role in normalize_ids(role_ids))


def can_propose(
    role_ids: Iterable[Any] | None,
    propose_role_ids: Iterable[Any] | None,
    *,
    is_platform_admin: bool = False,
    is_guild_admin: bool = False,
) -> bool:
    """Empty `propose_role_ids` means every member may submit."""
    if is_platform_admin or is_guild_admin:
        return True
    if not normalize_ids(propose_role_ids):
        return True
    return has_any_role(role_ids, propose_role_ids)


def can_moderate(
    role_ids: Iterable[Any] | None,
    mod_role_ids: Iterable[Any] | None,
    *,
    is_platform_admin: bool = False,
    is_guild_admin: bool = False,
) -> bool:
    """Mod roles, bridge admins (AdminService) or guild administrators."""
    if is_platform_admin or is_guild_admin:
        return True
    return has_any_role(role_ids, mod_role_ids)


def can_setup(
    *, is_platform_admin: bool = False, is_guild_admin: bool = False
) -> bool:
    return bool(is_platform_admin or is_guild_admin)


# --- duck-typed helpers over discord.Member ---------------------------------


def member_role_ids(member: Any) -> list[str]:
    return normalize_ids(getattr(member, "roles", ()) or ())


def is_guild_admin(member: Any) -> bool:
    perms = getattr(member, "guild_permissions", None)
    if perms is None:
        return False
    return bool(
        getattr(perms, "administrator", False)
        or getattr(perms, "manage_guild", False)
    )


def member_can_propose(
    member: Any,
    config: GuildConfig | None,
    *,
    is_platform_admin: bool = False,
) -> bool:
    return can_propose(
        member_role_ids(member),
        config.propose_role_ids if config else (),
        is_platform_admin=is_platform_admin,
        is_guild_admin=is_guild_admin(member),
    )


def member_can_moderate(
    member: Any,
    config: GuildConfig | None,
    *,
    is_platform_admin: bool = False,
) -> bool:
    return can_moderate(
        member_role_ids(member),
        config.mod_role_ids if config else (),
        is_platform_admin=is_platform_admin,
        is_guild_admin=is_guild_admin(member),
    )
