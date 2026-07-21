from __future__ import annotations

import threading
import unittest

from dataclasses import dataclass

from app.discord_access import DiscordAccessPolicy
from app.discord_commands import (
    DiscordCommandName,
    DiscordCommandRouter,
    DiscordRemoteStatus,
    format_discord_status,
    parse_discord_command,
)


@dataclass
class SessionSnapshot:
    active_session_count: int
    mapped_user_count: int


class FakeSessions:
    def __init__(self) -> None:
        self.new_calls = []
        self.status = SessionSnapshot(
            active_session_count=2,
            mapped_user_count=3,
        )

    def start_new_conversation(self, user_or_id, title=None):
        user_id = getattr(user_or_id, "id", user_or_id)
        self.new_calls.append((int(user_id), title))
        return 42

    def snapshot(self):
        return self.status


class User:
    def __init__(self, user_id):
        self.id = user_id


class DiscordCommandRouterTests(unittest.TestCase):
    def setUp(self):
        self.sessions = FakeSessions()
        self.stop_called = threading.Event()
        self.status = DiscordRemoteStatus(
            adapter_state="running",
            token_configured=True,
            allowed_user_count=1,
            active_session_count=2,
            mapped_user_count=3,
        )
        self.router = DiscordCommandRouter(
            conversation_sessions=self.sessions,
            access_policy=DiscordAccessPolicy([123]),
            status_provider=lambda: self.status,
            stop_callback=self.stop_called.set,
        )

    def test_normal_message_is_not_a_command(self):
        self.assertIsNone(self.router.handle(User(123), "hello Akira"))
        self.assertIsNone(parse_discord_command("hello"))

    def test_new_starts_fresh_user_conversation(self):
        result = self.router.handle(User(123), "/new")

        self.assertEqual(result.command, DiscordCommandName.NEW)
        self.assertEqual(result.reply, "Started a new Discord conversation.")
        self.assertFalse(result.stop_after_reply)
        self.assertEqual(
            self.sessions.new_calls,
            [(123, "Discord conversation")],
        )

    def test_new_accepts_optional_title(self):
        result = self.router.handle(User(123), "  /NEW College planning  ")

        self.assertEqual(result.command, DiscordCommandName.NEW)
        self.assertEqual(
            self.sessions.new_calls,
            [(123, "College planning")],
        )

    def test_status_returns_only_non_secret_state(self):
        result = self.router.handle(User(123), "/status")

        self.assertEqual(result.command, DiscordCommandName.STATUS)
        self.assertIn("Connection: running", result.reply)
        self.assertIn("Bot token: configured", result.reply)
        self.assertIn("Access: 1 allowed user", result.reply)
        self.assertIn("Runtime: 2 active sessions", result.reply)
        self.assertIn("Saved: 3 mapped conversations", result.reply)
        self.assertNotIn("123", result.reply)

    def test_status_formats_unavailable_token(self):
        text = format_discord_status(
            DiscordRemoteStatus(
                adapter_state="failed",
                token_configured=None,
                allowed_user_count=0,
                active_session_count=0,
                mapped_user_count=0,
            )
        )

        self.assertIn("Connection: failed", text)
        self.assertIn("Bot token: unavailable", text)
        self.assertIn("0 allowed users", text)

    def test_stop_is_deferred_until_schedule_stop(self):
        result = self.router.handle(User(123), "/stop")

        self.assertEqual(result.command, DiscordCommandName.STOP)
        self.assertTrue(result.stop_after_reply)
        self.assertFalse(self.stop_called.is_set())

        thread = self.router.schedule_stop()
        thread.join(timeout=1)
        self.assertTrue(self.stop_called.is_set())

    def test_arguments_show_usage_for_status_and_stop(self):
        status = self.router.handle(User(123), "/status extra")
        stop = self.router.handle(User(123), "/stop now")

        self.assertEqual(status.reply, "Usage: /status")
        self.assertEqual(stop.reply, "Usage: /stop")
        self.assertFalse(stop.stop_after_reply)

    def test_unknown_slash_command_returns_available_commands(self):
        result = self.router.handle(User(123), "/help")

        self.assertEqual(result.command, DiscordCommandName.UNKNOWN)
        self.assertIn("/new, /status, /stop", result.reply)


if __name__ == "__main__":
    unittest.main()
