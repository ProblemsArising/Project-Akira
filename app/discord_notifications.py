"""Outbound Discord notifications for Project Akira.

The notification service sends local application events to one or more
configured Discord users without routing the message through the LLM. Explicit
recipients must already be present in the default-deny Discord access policy;
omitting recipients broadcasts to every allowed user.

Rate-limit retries and richer delivery-error classification are intentionally
left to issue #76.
"""

from __future__ import annotations

import threading

from dataclasses import dataclass
from typing import Iterable, Protocol

from app.discord_access import (
    DiscordAccessPolicy,
    get_discord_access_policy,
    normalize_discord_user_id,
)
from app.discord_adapter import (
    DiscordAdapterService,
    get_discord_adapter_service,
)
from app.discord_dm import split_discord_message


MAX_NOTIFICATION_LENGTH = 20_000
MAX_NOTIFICATION_RECIPIENTS = 100


class DiscordNotificationAdapter(Protocol):
    """Adapter operation required by the notification service."""

    def send_direct_message(
        self,
        user_id: int,
        content: str,
        *,
        timeout: float = 10.0,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DiscordNotificationReport:
    """Non-sensitive summary of a completed outbound notification."""

    recipient_count: int
    message_parts: int
    messages_sent: int


class DiscordNotificationService:
    """Send notifications to configured Discord users."""

    def __init__(
        self,
        *,
        adapter: DiscordNotificationAdapter | None = None,
        access_policy: DiscordAccessPolicy | None = None,
    ) -> None:
        self._adapter = adapter or get_discord_adapter_service()
        self._access_policy = access_policy or get_discord_access_policy()

    def send(
        self,
        message: str,
        *,
        user_ids: Iterable[object] | None = None,
        timeout: float = 10.0,
    ) -> DiscordNotificationReport:
        """Send one notification to explicit recipients or all allowed users.

        Explicit recipients are validated as an all-or-nothing set before any
        message is sent. This prevents accidental delivery to a user outside
        the configured allow-list.
        """

        normalized_message = str(message).strip()
        if not normalized_message:
            raise ValueError("Discord notification message cannot be blank")
        if len(normalized_message) > MAX_NOTIFICATION_LENGTH:
            raise ValueError(
                "Discord notification message cannot exceed "
                f"{MAX_NOTIFICATION_LENGTH} characters"
            )
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        recipients = self._resolve_recipients(user_ids)
        parts = split_discord_message(normalized_message)

        for recipient in recipients:
            for part in parts:
                self._adapter.send_direct_message(
                    recipient,
                    part,
                    timeout=timeout,
                )

        return DiscordNotificationReport(
            recipient_count=len(recipients),
            message_parts=len(parts),
            messages_sent=len(recipients) * len(parts),
        )

    def _resolve_recipients(
        self,
        user_ids: Iterable[object] | None,
    ) -> tuple[int, ...]:
        allowed = frozenset(self._access_policy.allowed_user_ids)
        if not allowed:
            raise ValueError(
                "Configure at least one allowed Discord user before sending notifications."
            )

        if user_ids is None:
            return tuple(sorted(allowed))

        normalized = tuple(
            sorted({normalize_discord_user_id(value) for value in user_ids})
        )
        if not normalized:
            raise ValueError("Select at least one Discord notification recipient")
        if len(normalized) > MAX_NOTIFICATION_RECIPIENTS:
            raise ValueError(
                "Discord notifications cannot target more than "
                f"{MAX_NOTIFICATION_RECIPIENTS} users at once"
            )

        unauthorized = set(normalized) - allowed
        if unauthorized:
            raise ValueError(
                "Every Discord notification recipient must be in the allowed-user list."
            )
        return normalized


_DEFAULT_NOTIFICATION_SERVICE: DiscordNotificationService | None = None
_DEFAULT_NOTIFICATION_SERVICE_LOCK = threading.Lock()


def get_discord_notification_service() -> DiscordNotificationService:
    """Return the process-wide outbound notification service."""

    global _DEFAULT_NOTIFICATION_SERVICE
    with _DEFAULT_NOTIFICATION_SERVICE_LOCK:
        if _DEFAULT_NOTIFICATION_SERVICE is None:
            _DEFAULT_NOTIFICATION_SERVICE = DiscordNotificationService()
        return _DEFAULT_NOTIFICATION_SERVICE
