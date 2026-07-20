from __future__ import annotations

import sys
import types
import unittest

from app.discord_adapter import create_default_discord_client


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


class DiscordAdapterMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_client_enables_dm_events_and_forwards_messages(self):
        fake_discord = types.SimpleNamespace(
            Intents=FakeIntents,
            Client=FakeClient,
        )
        received = []

        async def handler(message):
            received.append(message)

        original = sys.modules.get("discord")
        sys.modules["discord"] = fake_discord
        try:
            client = create_default_discord_client(handler)
        finally:
            if original is None:
                sys.modules.pop("discord", None)
            else:
                sys.modules["discord"] = original

        self.assertTrue(client.intents.dm_messages)
        message = object()
        await client.events["on_message"](message)
        self.assertEqual(received, [message])


if __name__ == "__main__":
    unittest.main()
