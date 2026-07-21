from __future__ import annotations

import unittest

from app.discord_errors import (
    DiscordDeliveryError,
    DiscordPermissionError,
    DiscordRateLimitedError,
    DiscordRecipientNotFoundError,
    DiscordUnavailableError,
    classify_discord_delivery_error,
)


class FakeResponse:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}


class FakeHttpError(Exception):
    def __init__(self, status, secret="secret detail", headers=None):
        super().__init__(secret)
        self.status = status
        self.response = FakeResponse(status, headers)


class RateLimited(Exception):
    retry_after = 2.5


class DiscordErrorClassificationTests(unittest.TestCase):
    def test_http_categories_are_classified_without_raw_details(self):
        cases = [
            (FakeHttpError(403), DiscordPermissionError),
            (FakeHttpError(404), DiscordRecipientNotFoundError),
            (FakeHttpError(503), DiscordUnavailableError),
        ]

        for error, expected_type in cases:
            with self.subTest(status=error.status):
                classified = classify_discord_delivery_error(error)
                self.assertIsInstance(classified, expected_type)
                self.assertNotIn("secret detail", str(classified))

    def test_rate_limit_extracts_retry_delay(self):
        direct = classify_discord_delivery_error(RateLimited())
        self.assertIsInstance(direct, DiscordRateLimitedError)
        self.assertEqual(direct.retry_after, 2.5)

        header = classify_discord_delivery_error(
            FakeHttpError(429, headers={"Retry-After": "4"})
        )
        self.assertIsInstance(header, DiscordRateLimitedError)
        self.assertEqual(header.retry_after, 4.0)

    def test_network_and_unknown_errors_are_safe(self):
        self.assertIsInstance(
            classify_discord_delivery_error(OSError("private path")),
            DiscordUnavailableError,
        )
        unknown = classify_discord_delivery_error(
            RuntimeError("private implementation detail")
        )
        self.assertIsInstance(unknown, DiscordDeliveryError)
        self.assertNotIn("private implementation detail", str(unknown))


if __name__ == "__main__":
    unittest.main()
