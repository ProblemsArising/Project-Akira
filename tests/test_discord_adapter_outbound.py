from __future__ import annotations

import asyncio
import threading
import unittest

from app.discord_errors import (
    DiscordPermissionError,
    DiscordRateLimitedError,
)

from app.discord_adapter import (
    DiscordAdapterService,
    DiscordGatewayEvent,
)


class FakeUser:
    def __init__(self, user_id, error=None):
        self.id = user_id
        self.sent = []
        self.error = error

    async def send(self, content):
        if self.error is not None:
            raise self.error
        self.sent.append(content)


class FakeHttpError(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__("secret discord response")
        self.status = status
        self.retry_after = retry_after


class FakeOutboundClient:
    def __init__(self):
        self.start_called = threading.Event()
        self._close_event = None
        self._closed = False
        self.cached_users = {}
        self.fetched_users = {}
        self.fetch_calls = []
        self.latency = 0.01

    async def start(self, token, *, reconnect=True):
        self._close_event = asyncio.Event()
        self.start_called.set()
        await self._close_event.wait()

    async def close(self):
        self._closed = True
        if self._close_event is not None:
            self._close_event.set()

    def is_closed(self):
        return self._closed

    def get_user(self, user_id):
        return self.cached_users.get(user_id)

    async def fetch_user(self, user_id):
        self.fetch_calls.append(user_id)
        return self.fetched_users[user_id]


class DiscordAdapterOutboundTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeOutboundClient()
        self.service = DiscordAdapterService(client_factory=lambda: self.client)
        self.service.start("test-token", timeout=1)
        self.assertTrue(self.client.start_called.wait(timeout=1))

    def tearDown(self):
        if self.service.is_running:
            self.service.stop(timeout=1)

    def test_send_uses_cached_user_on_gateway_loop(self):
        user = FakeUser(123)
        self.client.cached_users[123] = user
        self.service.record_gateway_event(DiscordGatewayEvent.READY)

        self.service.send_direct_message(123, "Hello", timeout=1)

        self.assertEqual(user.sent, ["Hello"])
        self.assertEqual(self.client.fetch_calls, [])

    def test_send_fetches_user_when_not_cached(self):
        user = FakeUser(456)
        self.client.fetched_users[456] = user
        self.service.record_gateway_event(DiscordGatewayEvent.READY)

        self.service.send_direct_message(456, "Notification", timeout=1)

        self.assertEqual(self.client.fetch_calls, [456])
        self.assertEqual(user.sent, ["Notification"])

    def test_send_requires_ready_gateway(self):
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            self.service.send_direct_message(123, "Hello", timeout=1)

    def test_send_validates_user_content_and_timeout(self):
        self.service.record_gateway_event(DiscordGatewayEvent.READY)

        with self.assertRaises(ValueError):
            self.service.send_direct_message(0, "Hello")
        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            self.service.send_direct_message(123, "  ")
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            self.service.send_direct_message(123, "x" * 2_001)
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            self.service.send_direct_message(123, "Hello", timeout=0)

    def test_send_classifies_permission_and_rate_limit_errors(self):
        self.service.record_gateway_event(DiscordGatewayEvent.READY)

        self.client.cached_users[123] = FakeUser(
            123, error=FakeHttpError(403)
        )
        with self.assertRaises(DiscordPermissionError):
            self.service.send_direct_message(123, "Hello", timeout=1)

        self.client.cached_users[456] = FakeUser(
            456, error=FakeHttpError(429, retry_after=2.0)
        )
        with self.assertRaises(DiscordRateLimitedError) as context:
            self.service.send_direct_message(456, "Hello", timeout=1)
        self.assertEqual(context.exception.retry_after, 2.0)


if __name__ == "__main__":
    unittest.main()
