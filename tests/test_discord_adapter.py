from __future__ import annotations

import asyncio
import threading
import unittest

from app.discord_adapter import (
    DiscordAdapterService,
    DiscordAdapterState,
    get_discord_adapter_service,
)


class FakeDiscordClient:
    def __init__(self, *, start_error: Exception | None = None) -> None:
        self.start_error = start_error
        self.start_called = threading.Event()
        self.close_called = threading.Event()
        self.token = None
        self.reconnect = None
        self._closed = False
        self._close_requested = False
        self._close_event = None

    async def start(self, token: str, *, reconnect: bool = False) -> None:
        self.token = token
        self.reconnect = reconnect
        self._close_event = asyncio.Event()
        if self._close_requested:
            self._close_event.set()
        self.start_called.set()

        if self.start_error is not None:
            raise self.start_error

        await self._close_event.wait()

    async def close(self) -> None:
        self._closed = True
        self._close_requested = True
        self.close_called.set()
        if self._close_event is not None:
            self._close_event.set()

    def is_closed(self) -> bool:
        return self._closed


class DiscordAdapterServiceTests(unittest.TestCase):
    def test_start_and_stop_run_client_on_background_thread(self):
        client = FakeDiscordClient()
        service = DiscordAdapterService(client_factory=lambda: client)

        self.assertTrue(service.start("test-token", timeout=1))
        self.assertTrue(client.start_called.wait(timeout=1))
        self.assertEqual(service.state, DiscordAdapterState.RUNNING)
        self.assertTrue(service.is_running)
        self.assertEqual(client.token, "test-token")
        self.assertTrue(client.reconnect)

        self.assertTrue(service.stop(timeout=1))
        self.assertTrue(client.close_called.is_set())
        self.assertTrue(service.wait_until_stopped(timeout=1))
        self.assertEqual(service.state, DiscordAdapterState.STOPPED)
        self.assertFalse(service.is_running)

    def test_blank_token_is_rejected_without_creating_client(self):
        factories = []
        service = DiscordAdapterService(
            client_factory=lambda: factories.append(True) or FakeDiscordClient()
        )

        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            service.start("   ")

        self.assertEqual(factories, [])
        self.assertEqual(service.state, DiscordAdapterState.STOPPED)

    def test_second_start_is_ignored_while_active(self):
        client = FakeDiscordClient()
        service = DiscordAdapterService(client_factory=lambda: client)

        self.assertTrue(service.start("first", timeout=1))
        self.assertTrue(client.start_called.wait(timeout=1))
        self.assertFalse(service.start("second", timeout=1))
        self.assertEqual(client.token, "first")

        service.stop(timeout=1)

    def test_client_failure_is_exposed_without_crashing_caller_thread(self):
        errors = []
        client = FakeDiscordClient(start_error=RuntimeError("gateway unavailable"))
        service = DiscordAdapterService(
            client_factory=lambda: client,
            on_error=errors.append,
        )

        self.assertTrue(service.start("test-token", timeout=1))
        self.assertTrue(service.wait_until_stopped(timeout=1))

        snapshot = service.snapshot()
        self.assertEqual(snapshot.state, DiscordAdapterState.FAILED)
        self.assertFalse(snapshot.running)
        self.assertEqual(snapshot.last_error, "gateway unavailable")
        self.assertEqual(len(errors), 1)
        self.assertEqual(str(errors[0]), "gateway unavailable")

    def test_factory_failure_is_reported_from_start(self):
        def broken_factory():
            raise RuntimeError("discord dependency unavailable")

        service = DiscordAdapterService(client_factory=broken_factory)

        with self.assertRaisesRegex(RuntimeError, "failed to start"):
            service.start("test-token", timeout=1)

        self.assertTrue(service.wait_until_stopped(timeout=1))
        self.assertEqual(service.state, DiscordAdapterState.FAILED)
        self.assertEqual(
            str(service.last_error),
            "discord dependency unavailable",
        )

    def test_stopped_service_returns_false_from_stop(self):
        service = DiscordAdapterService(client_factory=FakeDiscordClient)
        self.assertFalse(service.stop(timeout=1))

    def test_default_service_is_process_wide_singleton(self):
        self.assertIs(
            get_discord_adapter_service(),
            get_discord_adapter_service(),
        )


if __name__ == "__main__":
    unittest.main()
