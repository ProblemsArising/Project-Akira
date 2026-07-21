from __future__ import annotations

import unittest

from app.discord_access import DiscordAccessPolicy
from app.discord_errors import (
    DiscordPermissionError,
    DiscordRateLimitedError,
)
from app.discord_notifications import (
    DiscordNotificationDeliveryFailed,
    DiscordNotificationRateLimited,
    DiscordNotificationService,
)
from app.discord_rate_limit import DiscordRateLimiter


class SelectiveAdapter:
    def __init__(self, errors=None):
        self.errors = errors or {}
        self.calls = []

    def send_direct_message(self, user_id, content, *, timeout=10.0):
        self.calls.append((user_id, content))
        error = self.errors.get(user_id)
        if error is not None:
            raise error


class DiscordNotificationErrorTests(unittest.TestCase):
    def test_partial_recipient_failure_returns_safe_counts(self):
        adapter = SelectiveAdapter({200: DiscordPermissionError()})
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([100, 200]),
        )

        report = service.send("hello")

        self.assertEqual(report.messages_sent, 1)
        self.assertEqual(report.messages_failed, 1)
        self.assertTrue(report.partial_failure)
        self.assertEqual(report.messages_attempted, 2)

    def test_all_delivery_failures_raise_safe_summary(self):
        secret = "never expose this"
        adapter = SelectiveAdapter({100: RuntimeError(secret)})
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([100]),
        )

        with self.assertRaises(DiscordNotificationDeliveryFailed) as context:
            service.send("hello")

        self.assertEqual(context.exception.messages_failed, 1)
        self.assertNotIn(secret, str(context.exception))

    def test_remote_rate_limit_stops_delivery_and_preserves_retry_after(self):
        adapter = SelectiveAdapter({100: DiscordRateLimitedError(3.5)})
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([100, 200]),
        )

        with self.assertRaises(DiscordRateLimitedError) as context:
            service.send("hello")

        self.assertEqual(context.exception.retry_after, 3.5)
        self.assertEqual(adapter.calls, [(100, "hello")])

    def test_local_message_budget_is_enforced_before_delivery(self):
        adapter = SelectiveAdapter()
        limiter = DiscordRateLimiter(limit=1, window_seconds=60)
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([100]),
            rate_limiter=limiter,
        )

        service.send("first")
        with self.assertRaises(DiscordNotificationRateLimited) as context:
            service.send("second")

        self.assertGreater(context.exception.retry_after, 0)
        self.assertEqual(adapter.calls, [(100, "first")])


if __name__ == "__main__":
    unittest.main()
