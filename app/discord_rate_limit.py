"""Thread-safe local rate limiting for Discord remote operations."""

from __future__ import annotations

import math
import threading
import time

from collections import deque
from dataclasses import dataclass
from typing import Callable, Hashable


Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class DiscordRateLimitDecision:
    """Result of consuming capacity from a sliding-window limiter."""

    allowed: bool
    retry_after: float = 0.0
    remaining: int = 0


class DiscordRateLimiter:
    """Bounded per-key sliding-window limiter.

    Each accepted operation records one or more cost units. Old entries expire
    after ``window_seconds``. Keys are removed when their window becomes empty,
    which prevents an unbounded collection of inactive Discord user IDs.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float,
        clock: Clock | None = None,
    ) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("rate limit must be a positive integer")
        if window_seconds <= 0:
            raise ValueError("rate-limit window must be greater than zero")

        self.limit = limit
        self.window_seconds = float(window_seconds)
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._events: dict[Hashable, deque[tuple[float, int]]] = {}

    def consume(
        self,
        key: Hashable,
        *,
        cost: int = 1,
    ) -> DiscordRateLimitDecision:
        """Consume capacity and return when the next request may be retried."""

        if isinstance(cost, bool) or not isinstance(cost, int) or cost <= 0:
            raise ValueError("rate-limit cost must be a positive integer")
        if cost > self.limit:
            return DiscordRateLimitDecision(
                allowed=False,
                retry_after=self.window_seconds,
                remaining=self.limit,
            )

        now = float(self._clock())
        cutoff = now - self.window_seconds

        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0][0] <= cutoff:
                events.popleft()

            used = sum(event_cost for _, event_cost in events)
            if used + cost > self.limit:
                retry_after = max(
                    0.0,
                    self.window_seconds - (now - events[0][0]),
                )
                return DiscordRateLimitDecision(
                    allowed=False,
                    retry_after=retry_after,
                    remaining=max(0, self.limit - used),
                )

            events.append((now, cost))
            remaining = self.limit - used - cost
            return DiscordRateLimitDecision(
                allowed=True,
                retry_after=0.0,
                remaining=remaining,
            )

    def reset(self, key: Hashable | None = None) -> None:
        """Clear one key or every tracked rate-limit window."""

        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)

    @staticmethod
    def retry_after_seconds(decision: DiscordRateLimitDecision) -> int:
        """Round a retry delay up for user-facing messages and HTTP headers."""

        return max(1, int(math.ceil(decision.retry_after)))
