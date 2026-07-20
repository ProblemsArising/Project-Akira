"""Discord user-ID authorization for Project Akira.

Remote Discord access is default-deny. A user is accepted only when their
numeric Discord user ID appears in the configured allow-list.

This module deliberately has no dependency on ``discord.py``. Callers may pass
an integer/string ID or any Discord-like object exposing an ``id`` attribute.
The future Discord settings page can update the same policy at runtime.
"""

from __future__ import annotations

import threading

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


MAX_DISCORD_SNOWFLAKE = (1 << 64) - 1


class DiscordAccessReason(str, Enum):
    """Reason returned by an authorization decision."""

    ALLOWED = "allowed"
    NOT_CONFIGURED = "not_configured"
    USER_NOT_ALLOWED = "user_not_allowed"
    INVALID_USER_ID = "invalid_user_id"


@dataclass(frozen=True, slots=True)
class DiscordAccessDecision:
    """Non-sensitive result suitable for handlers, APIs, and diagnostics."""

    allowed: bool
    reason: DiscordAccessReason


@dataclass(frozen=True, slots=True)
class DiscordAccessSnapshot:
    """Safe public policy status that does not enumerate user IDs."""

    configured: bool
    allowed_user_count: int
    default_deny: bool = True


class DiscordAccessDenied(PermissionError):
    """Raised when a Discord user is not authorized to control Akira."""

    def __init__(self, reason: DiscordAccessReason) -> None:
        self.reason = reason
        super().__init__("Discord user is not authorized to access Project Akira.")


def normalize_discord_user_id(value: object) -> int:
    """Return a validated Discord snowflake as an integer.

    ``bool`` values are rejected even though ``bool`` is an ``int`` subclass.
    Strings must contain decimal digits only. Discord snowflakes are unsigned
    64-bit values, and zero is not a valid user ID.
    """

    if hasattr(value, "id") and not isinstance(value, (str, bytes, int)):
        value = getattr(value, "id")

    if isinstance(value, bool):
        raise ValueError("Discord user ID must be a positive integer")

    if isinstance(value, int):
        user_id = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized or not normalized.isascii() or not normalized.isdecimal():
            raise ValueError("Discord user ID must contain decimal digits only")
        user_id = int(normalized, 10)
    else:
        raise ValueError("Discord user ID must be an integer or decimal string")

    if user_id <= 0 or user_id > MAX_DISCORD_SNOWFLAKE:
        raise ValueError("Discord user ID is outside the valid snowflake range")

    return user_id


class DiscordAccessPolicy:
    """Thread-safe, runtime-updatable Discord user allow-list."""

    def __init__(self, allowed_user_ids: Iterable[object] = ()) -> None:
        self._lock = threading.RLock()
        self._allowed_user_ids: frozenset[int] = frozenset()
        self.replace_allowed_user_ids(allowed_user_ids)

    @property
    def allowed_user_ids(self) -> tuple[int, ...]:
        """Return the normalized IDs in deterministic order."""

        with self._lock:
            return tuple(sorted(self._allowed_user_ids))

    def snapshot(self) -> DiscordAccessSnapshot:
        """Return policy status without enumerating the configured IDs."""

        with self._lock:
            count = len(self._allowed_user_ids)

        return DiscordAccessSnapshot(
            configured=count > 0,
            allowed_user_count=count,
        )

    def replace_allowed_user_ids(self, values: Iterable[object]) -> tuple[int, ...]:
        """Atomically replace the allow-list after validating every value."""

        if values is None:
            raise ValueError("Allowed Discord user IDs must be an iterable")

        # Normalize completely before taking the lock so invalid updates leave
        # the existing security policy untouched.
        normalized = frozenset(normalize_discord_user_id(value) for value in values)

        with self._lock:
            self._allowed_user_ids = normalized
            return tuple(sorted(normalized))

    def add_allowed_user_id(self, value: object) -> bool:
        """Add one ID and report whether the policy changed."""

        user_id = normalize_discord_user_id(value)
        with self._lock:
            if user_id in self._allowed_user_ids:
                return False
            self._allowed_user_ids = self._allowed_user_ids | {user_id}
            return True

    def remove_allowed_user_id(self, value: object) -> bool:
        """Remove one ID and report whether the policy changed."""

        user_id = normalize_discord_user_id(value)
        with self._lock:
            if user_id not in self._allowed_user_ids:
                return False
            self._allowed_user_ids = self._allowed_user_ids - {user_id}
            return True

    def clear(self) -> bool:
        """Remove every allowed ID, returning to the default-deny state."""

        with self._lock:
            if not self._allowed_user_ids:
                return False
            self._allowed_user_ids = frozenset()
            return True

    def authorize(self, user_or_id: object) -> DiscordAccessDecision:
        """Return a default-deny authorization decision."""

        try:
            user_id = normalize_discord_user_id(user_or_id)
        except (AttributeError, TypeError, ValueError):
            return DiscordAccessDecision(
                allowed=False,
                reason=DiscordAccessReason.INVALID_USER_ID,
            )

        with self._lock:
            allowed_user_ids = self._allowed_user_ids

        if not allowed_user_ids:
            return DiscordAccessDecision(
                allowed=False,
                reason=DiscordAccessReason.NOT_CONFIGURED,
            )

        if user_id not in allowed_user_ids:
            return DiscordAccessDecision(
                allowed=False,
                reason=DiscordAccessReason.USER_NOT_ALLOWED,
            )

        return DiscordAccessDecision(
            allowed=True,
            reason=DiscordAccessReason.ALLOWED,
        )

    def is_allowed(self, user_or_id: object) -> bool:
        """Return only the authorization boolean for convenient filtering."""

        return self.authorize(user_or_id).allowed

    def require_allowed(self, user_or_id: object) -> None:
        """Raise ``DiscordAccessDenied`` unless the user is authorized."""

        decision = self.authorize(user_or_id)
        if not decision.allowed:
            raise DiscordAccessDenied(decision.reason)


_DEFAULT_ACCESS_POLICY: DiscordAccessPolicy | None = None
_DEFAULT_ACCESS_POLICY_LOCK = threading.Lock()


def get_discord_access_policy() -> DiscordAccessPolicy:
    """Return the process-wide Discord access policy."""

    global _DEFAULT_ACCESS_POLICY
    with _DEFAULT_ACCESS_POLICY_LOCK:
        if _DEFAULT_ACCESS_POLICY is None:
            _DEFAULT_ACCESS_POLICY = DiscordAccessPolicy()
        return _DEFAULT_ACCESS_POLICY
