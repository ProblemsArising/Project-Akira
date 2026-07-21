"""Rate-limited outbound Discord notifications for Project Akira."""

from __future__ import annotations

import threading

from dataclasses import dataclass
from typing import Iterable, Protocol

from app.discord_access import (
    DiscordAccessPolicy,
    get_discord_access_policy,
    normalize_discord_user_id,
)
from app.discord_adapter import get_discord_adapter_service
from app.discord_dm import split_discord_message
from app.discord_errors import (
    DiscordDeliveryError,
    DiscordRateLimitedError,
)
from app.discord_rate_limit import DiscordRateLimiter


MAX_NOTIFICATION_LENGTH = 20_000
MAX_NOTIFICATION_RECIPIENTS = 100
DEFAULT_NOTIFICATION_MESSAGE_LIMIT = 30
DEFAULT_NOTIFICATION_WINDOW_SECONDS = 60.0


class DiscordNotificationAdapter(Protocol):
    def send_direct_message(
        self,
        user_id: int,
        content: str,
        *,
        timeout: float = 10.0,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DiscordNotificationReport:
    """Non-sensitive summary of completed outbound delivery attempts."""

    recipient_count: int
    message_parts: int
    messages_sent: int
    messages_failed: int = 0

    @property
    def messages_attempted(self) -> int:
        return self.messages_sent + self.messages_failed

    @property
    def partial_failure(self) -> bool:
        return self.messages_sent > 0 and self.messages_failed > 0


class DiscordNotificationRateLimited(DiscordRateLimitedError):
    """The local outbound notification budget has been exhausted."""


class DiscordNotificationDeliveryFailed(DiscordDeliveryError):
    """Every attempted outbound message failed safely."""

    def __init__(self, *, messages_failed: int) -> None:
        self.messages_failed = messages_failed
        super().__init__("Discord could not deliver the notification.")


class DiscordNotificationService:
    """Send bounded notifications to configured Discord users."""

    def __init__(
        self,
        *,
        adapter: DiscordNotificationAdapter | None = None,
        access_policy: DiscordAccessPolicy | None = None,
        rate_limiter: DiscordRateLimiter | None = None,
    ) -> None:
        self._adapter = adapter or get_discord_adapter_service()
        self._access_policy = access_policy or get_discord_access_policy()
        self._rate_limiter = rate_limiter or DiscordRateLimiter(
            limit=DEFAULT_NOTIFICATION_MESSAGE_LIMIT,
            window_seconds=DEFAULT_NOTIFICATION_WINDOW_SECONDS,
        )

    def send(
        self,
        message: str,
        *,
        user_ids: Iterable[object] | None = None,
        timeout: float = 10.0,
    ) -> DiscordNotificationReport:
        """Send one notification with local throttling and partial-failure isolation."""

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
        message_count = len(recipients) * len(parts)

        decision = self._rate_limiter.consume(
            "outbound_notifications",
            cost=message_count,
        )
        if not decision.allowed:
            raise DiscordNotificationRateLimited(decision.retry_after)

        sent = 0
        failed = 0
        for recipient in recipients:
            for part in parts:
                try:
                    self._adapter.send_direct_message(
                        recipient,
                        part,
                        timeout=timeout,
                    )
                except DiscordRateLimitedError:
                    # Stop immediately: a surfaced remote rate limit can apply
                    # beyond one recipient. discord.py normally handles 429s
                    # internally, but this remains safe when it cannot wait.
                    raise
                except DiscordDeliveryError:
                    failed += 1
                except Exception:
                    # Adapter implementations injected by extensions/tests are
                    # isolated without exposing their raw exception text.
                    failed += 1
                else:
                    sent += 1

        if sent == 0 and failed:
            raise DiscordNotificationDeliveryFailed(messages_failed=failed)

        return DiscordNotificationReport(
            recipient_count=len(recipients),
            message_parts=len(parts),
            messages_sent=sent,
            messages_failed=failed,
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
    global _DEFAULT_NOTIFICATION_SERVICE
    with _DEFAULT_NOTIFICATION_SERVICE_LOCK:
        if _DEFAULT_NOTIFICATION_SERVICE is None:
            _DEFAULT_NOTIFICATION_SERVICE = DiscordNotificationService()
        return _DEFAULT_NOTIFICATION_SERVICE
