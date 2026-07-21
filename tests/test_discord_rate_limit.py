from __future__ import annotations

import unittest

from app.discord_rate_limit import DiscordRateLimiter


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class DiscordRateLimiterTests(unittest.TestCase):
    def test_sliding_window_limits_each_key_independently(self):
        clock = FakeClock()
        limiter = DiscordRateLimiter(
            limit=2,
            window_seconds=10,
            clock=clock,
        )

        self.assertTrue(limiter.consume("user-a").allowed)
        self.assertTrue(limiter.consume("user-a").allowed)
        denied = limiter.consume("user-a")
        self.assertFalse(denied.allowed)
        self.assertEqual(limiter.retry_after_seconds(denied), 10)
        self.assertTrue(limiter.consume("user-b").allowed)

        clock.advance(10)
        self.assertTrue(limiter.consume("user-a").allowed)

    def test_cost_and_reset_are_supported(self):
        clock = FakeClock()
        limiter = DiscordRateLimiter(limit=5, window_seconds=60, clock=clock)

        accepted = limiter.consume("notifications", cost=4)
        self.assertTrue(accepted.allowed)
        self.assertEqual(accepted.remaining, 1)
        self.assertFalse(limiter.consume("notifications", cost=2).allowed)

        limiter.reset("notifications")
        self.assertTrue(limiter.consume("notifications", cost=5).allowed)
        self.assertFalse(limiter.consume("notifications").allowed)

    def test_invalid_configuration_and_cost_are_rejected(self):
        with self.assertRaises(ValueError):
            DiscordRateLimiter(limit=0, window_seconds=1)
        with self.assertRaises(ValueError):
            DiscordRateLimiter(limit=1, window_seconds=0)

        limiter = DiscordRateLimiter(limit=1, window_seconds=1)
        with self.assertRaises(ValueError):
            limiter.consume("key", cost=0)


if __name__ == "__main__":
    unittest.main()
