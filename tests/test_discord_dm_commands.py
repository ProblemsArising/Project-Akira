from __future__ import annotations

import threading
import unittest

from app.discord_access import DiscordAccessPolicy
from app.discord_commands import (
    DiscordCommandRouter,
    DiscordRemoteStatus,
)
from app.discord_dm import DiscordDMHandler, DiscordDMOutcome


class SessionSnapshot:
    active_session_count = 0
    mapped_user_count = 0


class FakeSessions:
    def __init__(self):
        self.new_calls = []
        self.process_calls = []

    def start_new_conversation(self, user_or_id, title=None):
        self.new_calls.append((user_or_id.id, title))
        return 99

    def process_text(self, user_or_id, text, *, speak=False, source="text"):
        self.process_calls.append((user_or_id.id, text, speak, source))
        raise AssertionError("Commands must not reach the conversation model")

    def snapshot(self):
        return SessionSnapshot()


class Author:
    bot = False

    def __init__(self, user_id):
        self.id = user_id


class Channel:
    def __init__(self, events=None):
        self.sent = []
        self.events = events

    async def send(self, content):
        self.sent.append(content)
        if self.events is not None:
            self.events.append(("send", content))


class Message:
    guild = None

    def __init__(self, user_id, content, channel=None):
        self.author = Author(user_id)
        self.content = content
        self.channel = channel or Channel()


class DiscordDMCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, sessions, *, stop_callback=lambda: None):
        router = DiscordCommandRouter(
            conversation_sessions=sessions,
            access_policy=DiscordAccessPolicy([123]),
            status_provider=lambda: DiscordRemoteStatus(
                adapter_state="running",
                token_configured=True,
                allowed_user_count=1,
                active_session_count=0,
                mapped_user_count=0,
            ),
            stop_callback=stop_callback,
        )
        return DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123]),
            conversation_sessions=sessions,
            command_router=router,
        )

    async def test_new_command_starts_conversation_without_calling_llm(self):
        sessions = FakeSessions()
        handler = self.make_handler(sessions)
        message = Message(123, "/new Road trip")

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.COMMAND)
        self.assertEqual(
            message.channel.sent,
            ["Started a new Discord conversation."],
        )
        self.assertEqual(sessions.new_calls, [(123, "Road trip")])
        self.assertEqual(sessions.process_calls, [])

    async def test_status_command_does_not_create_conversation(self):
        sessions = FakeSessions()
        handler = self.make_handler(sessions)
        message = Message(123, "/status")

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.COMMAND)
        self.assertIn("Connection: running", message.channel.sent[0])
        self.assertEqual(sessions.new_calls, [])
        self.assertEqual(sessions.process_calls, [])

    async def test_stop_confirmation_is_sent_before_stop_callback_runs(self):
        sessions = FakeSessions()
        events = []
        stop_finished = threading.Event()

        def stop():
            events.append(("stop", None))
            stop_finished.set()

        handler = self.make_handler(sessions, stop_callback=stop)
        message = Message(123, "/stop", channel=Channel(events))

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.COMMAND)
        self.assertTrue(stop_finished.wait(timeout=1))
        self.assertEqual(events[0][0], "send")
        self.assertEqual(events[1][0], "stop")
        self.assertEqual(
            message.channel.sent,
            ["Stopping Discord remote messaging."],
        )

    async def test_unauthorized_user_cannot_run_commands(self):
        sessions = FakeSessions()
        handler = self.make_handler(sessions)
        message = Message(999, "/new")

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.DENIED)
        self.assertEqual(message.channel.sent, [])
        self.assertEqual(sessions.new_calls, [])


if __name__ == "__main__":
    unittest.main()
