"""Safe Discord delivery-error classification.

The public exception messages intentionally exclude raw Discord response text,
credentials, recipient IDs, model output, and filesystem details.
"""

from __future__ import annotations

import math
from typing import Any


class DiscordDeliveryError(RuntimeError):
    """Base class for a Discord delivery failure safe to expose by category."""


class DiscordRateLimitedError(DiscordDeliveryError):
    """Discord or the local application requested a temporary pause."""

    def __init__(self, retry_after: float) -> None:
        normalized = _finite_positive_float(retry_after) or 1.0
        self.retry_after = normalized
        super().__init__("Discord delivery is temporarily rate limited.")


class DiscordPermissionError(DiscordDeliveryError):
    """The bot cannot send to the requested recipient."""

    def __init__(self) -> None:
        super().__init__("Discord rejected delivery for permission reasons.")


class DiscordRecipientNotFoundError(DiscordDeliveryError):
    """The requested Discord recipient no longer exists or is unavailable."""

    def __init__(self) -> None:
        super().__init__("Discord recipient could not be resolved.")


class DiscordUnavailableError(DiscordDeliveryError):
    """A temporary Discord, network, gateway, or timeout failure occurred."""

    def __init__(self) -> None:
        super().__init__("Discord delivery is temporarily unavailable.")


def _finite_positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _status_code(error: BaseException) -> int | None:
    for value in (
        getattr(error, "status", None),
        getattr(getattr(error, "response", None), "status", None),
    ):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _retry_after(error: BaseException) -> float | None:
    direct = _finite_positive_float(getattr(error, "retry_after", None))
    if direct is not None:
        return direct

    response = getattr(error, "response", None)
    headers: Any = getattr(response, "headers", None)
    if headers is None:
        return None

    try:
        raw = headers.get("Retry-After")
    except (AttributeError, TypeError):
        return None
    return _finite_positive_float(raw)


def classify_discord_delivery_error(
    error: BaseException,
) -> DiscordDeliveryError:
    """Translate library/network exceptions into stable non-secret categories."""

    if isinstance(error, DiscordDeliveryError):
        return error

    status = _status_code(error)
    type_name = type(error).__name__.casefold()

    if status == 429 or "ratelimit" in type_name or "rate_limited" in type_name:
        return DiscordRateLimitedError(_retry_after(error) or 1.0)
    if status == 403 or type_name == "forbidden":
        return DiscordPermissionError()
    if status == 404 or type_name == "notfound":
        return DiscordRecipientNotFoundError()
    if status is not None and status >= 500:
        return DiscordUnavailableError()
    if isinstance(error, (TimeoutError, OSError, ConnectionError)):
        return DiscordUnavailableError()
    return DiscordDeliveryError("Discord could not deliver that message.")
