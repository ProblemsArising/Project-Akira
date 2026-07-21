from __future__ import annotations

import tempfile
import unittest

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.discord_api import register_discord_routes
from app.discord_errors import DiscordRateLimitedError
from app.discord_notifications import (
    DiscordNotificationDeliveryFailed,
    DiscordNotificationReport,
)


class FakeNotificationService:
    def __init__(self):
        self.calls = []
        self.error = None

    def send(self, message, *, user_ids=None, timeout=10.0):
        self.calls.append(
            {
                "message": message,
                "user_ids": user_ids,
                "timeout": timeout,
            }
        )
        if self.error is not None:
            raise self.error
        recipients = 2 if user_ids is None else len(set(user_ids))
        return DiscordNotificationReport(
            recipient_count=recipients,
            message_parts=1,
            messages_sent=recipients,
        )


class DiscordNotificationApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        web_root = Path(self.temporary.name) / "web"
        web_root.mkdir()
        (web_root / "index.html").write_text("Discord", encoding="utf-8")

        self.service = FakeNotificationService()
        app = FastAPI()
        app.state.discord_notifications = self.service
        register_discord_routes(app, web_root)
        self.client = TestClient(app)

    def tearDown(self):
        self.temporary.cleanup()

    def test_notification_broadcast_returns_delivery_counts(self):
        response = self.client.post(
            "/api/discord/notifications",
            json={"message": "Backup finished."},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "recipient_count": 2,
                "message_parts": 1,
                "messages_sent": 2,
                "messages_failed": 0,
                "partial_failure": False,
            },
        )
        self.assertEqual(self.service.calls[0]["user_ids"], None)

    def test_explicit_recipients_are_forwarded_as_strings(self):
        response = self.client.post(
            "/api/discord/notifications",
            json={
                "message": "Hello",
                "user_ids": ["123", "456"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.service.calls[0]["user_ids"],
            ["123", "456"],
        )

    def test_validation_and_service_errors_are_safe(self):
        blank = self.client.post(
            "/api/discord/notifications",
            json={"message": ""},
        )
        self.assertEqual(blank.status_code, 422)

        self.service.error = ValueError("Recipient is not allowed")
        invalid = self.client.post(
            "/api/discord/notifications",
            json={"message": "Hello"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertIn("not allowed", invalid.json()["detail"])

        self.service.error = RuntimeError("secret gateway failure")
        unavailable = self.client.post(
            "/api/discord/notifications",
            json={"message": "Hello"},
        )
        self.assertEqual(unavailable.status_code, 503)
        self.assertNotIn("secret gateway failure", unavailable.text)

    def test_partial_delivery_counts_are_returned(self):
        self.service.send = lambda *args, **kwargs: DiscordNotificationReport(
            recipient_count=2,
            message_parts=1,
            messages_sent=1,
            messages_failed=1,
        )

        response = self.client.post(
            "/api/discord/notifications",
            json={"message": "Hello"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["messages_failed"], 1)
        self.assertTrue(response.json()["partial_failure"])

    def test_rate_limit_returns_429_and_retry_after(self):
        self.service.error = DiscordRateLimitedError(2.2)

        response = self.client.post(
            "/api/discord/notifications",
            json={"message": "Hello"},
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "3")
        self.assertNotIn("secret", response.text)

    def test_total_delivery_failure_returns_safe_503(self):
        self.service.error = DiscordNotificationDeliveryFailed(
            messages_failed=2
        )

        response = self.client.post(
            "/api/discord/notifications",
            json={"message": "Hello"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("2", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
