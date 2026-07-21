from __future__ import annotations

import asyncio
import threading
import time
import unittest

from app.discord_adapter import (
    DiscordAdapterHealth,
    DiscordAdapterService,
    DiscordGatewayEvent,
)


class FakeDiscordClient:
    def __init__(self) -> None:
        self.start_called = threading.Event()
        self._close_event = None
        self._closed = False
        self.latency = 0.125
        self.reconnect = None

    async def start(self, token, *, reconnect=True):
        self.reconnect = reconnect
        self._close_event = asyncio.Event()
        self.start_called.set()
        await self._close_event.wait()

    async def close(self):
        self._closed = True
        if self._close_event is not None:
            self._close_event.set()

    def is_closed(self):
        return self._closed


class DiscordAdapterHealthTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeDiscordClient()
        self.service = DiscordAdapterService(
            client_factory=lambda: self.client
        )
        self.service.start("test-token", timeout=1)
        self.assertTrue(self.client.start_called.wait(timeout=1))

    def tearDown(self):
        if self.service.is_running:
            self.service.stop(timeout=1)

    def test_gateway_events_update_health_latency_and_reconnect_counts(self):
        initial = self.service.snapshot()
        self.assertEqual(initial.health, DiscordAdapterHealth.CONNECTING)
        self.assertFalse(initial.connected)
        self.assertTrue(initial.reconnect_enabled)
        self.assertIsNotNone(initial.uptime_seconds)

        self.service.record_gateway_event(DiscordGatewayEvent.CONNECTED)
        connected = self.service.snapshot()
        self.assertTrue(connected.connected)
        self.assertFalse(connected.ready)
        self.assertEqual(connected.health, DiscordAdapterHealth.CONNECTING)

        self.service.record_gateway_event(DiscordGatewayEvent.READY)
        ready = self.service.snapshot()
        self.assertTrue(ready.connected)
        self.assertTrue(ready.ready)
        self.assertEqual(ready.health, DiscordAdapterHealth.HEALTHY)
        self.assertEqual(ready.latency_ms, 125.0)

        self.service.record_gateway_event(DiscordGatewayEvent.DISCONNECTED)
        disconnected = self.service.snapshot()
        self.assertFalse(disconnected.connected)
        self.assertFalse(disconnected.ready)
        self.assertEqual(
            disconnected.health,
            DiscordAdapterHealth.RECONNECTING,
        )
        self.assertEqual(disconnected.disconnect_count, 1)
        self.assertEqual(disconnected.reconnect_count, 0)

        self.service.record_gateway_event(DiscordGatewayEvent.RESUMED)
        recovered = self.service.snapshot()
        self.assertEqual(recovered.health, DiscordAdapterHealth.HEALTHY)
        self.assertEqual(recovered.disconnect_count, 1)
        self.assertEqual(recovered.reconnect_count, 1)

    def test_repeated_ready_event_without_disconnect_is_not_reconnect(self):
        self.service.record_gateway_event(DiscordGatewayEvent.READY)
        self.service.record_gateway_event(DiscordGatewayEvent.READY)

        self.assertEqual(self.service.snapshot().reconnect_count, 0)

    def test_stop_does_not_count_close_as_disconnect(self):
        self.service.record_gateway_event(DiscordGatewayEvent.READY)
        self.service.stop(timeout=1)
        self.service.record_gateway_event(DiscordGatewayEvent.DISCONNECTED)

        snapshot = self.service.snapshot()
        self.assertEqual(snapshot.health, DiscordAdapterHealth.STOPPED)
        self.assertEqual(snapshot.disconnect_count, 0)
        self.assertIsNone(snapshot.uptime_seconds)


if __name__ == "__main__":
    unittest.main()
