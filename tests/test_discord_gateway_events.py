from __future__ import annotations

import sys
import types
import unittest

from app.discord_adapter import (
    DiscordGatewayEvent,
    create_default_discord_client,
)


class FakeIntents:
    def __init__(self):
        self.dm_messages = False

    @classmethod
    def none(cls):
        return cls()


class FakeClient:
    def __init__(self, *, intents):
        self.intents = intents
        self.events = {}

    def event(self, callback):
        self.events[callback.__name__] = callback
        return callback


class DiscordGatewayEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_client_forwards_gateway_health_events(self):
        fake_discord = types.SimpleNamespace(
            Intents=FakeIntents,
            Client=FakeClient,
        )
        events = []

        original = sys.modules.get("discord")
        sys.modules["discord"] = fake_discord
        try:
            client = create_default_discord_client(
                gateway_event_handler=events.append,
            )
        finally:
            if original is None:
                sys.modules.pop("discord", None)
            else:
                sys.modules["discord"] = original

        await client.events["on_connect"]()
        await client.events["on_ready"]()
        await client.events["on_disconnect"]()
        await client.events["on_resumed"]()

        self.assertEqual(
            events,
            [
                DiscordGatewayEvent.CONNECTED,
                DiscordGatewayEvent.READY,
                DiscordGatewayEvent.DISCONNECTED,
                DiscordGatewayEvent.RESUMED,
            ],
        )


if __name__ == "__main__":
    unittest.main()
