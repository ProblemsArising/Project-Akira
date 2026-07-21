from __future__ import annotations

import unittest

from dataclasses import dataclass

from app.discord_access import DiscordAccessPolicy
from app.discord_dm import DiscordDMHandler, DiscordDMOutcome
from app.discord_rate_limit import DiscordRateLimiter


@dataclass
class FakeResult:
    reply: str


class FakeSessions:
    def __init__(self):
        self.calls = []

    def process_text(self, user, text, **kwargs):
        self.calls.append((user.id, text, kwargs))
        return FakeResult("reply")


class Author:
    def __init__(self, user_id):
        self.id = user_id
        self.bot = False


class Channel:
    def __init__(self, *, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, text):
        if self.fail:
            raise RuntimeError("secret send failure")
        self.sent.append(text)


class Message:
    guild = None

    def __init__(self, user_id=123, content="hello", channel=None):
        self.author = Author(user_id)
        self.content = content
        self.channel = channel or Channel()


class DiscordDMRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_user_is_limited_before_llm_processing(self):
        sessions = FakeSessions()
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123]),
            conversation_sessions=sessions,
            rate_limiter=DiscordRateLimiter(limit=1, window_seconds=60),
        )

        first = Message(content="first")
        second = Message(content="second")
        self.assertEqual(
            (await handler.handle_message(first)).outcome,
            DiscordDMOutcome.REPLIED,
        )
        limited = await handler.handle_message(second)

        self.assertEqual(limited.outcome, DiscordDMOutcome.RATE_LIMITED)
        self.assertEqual(len(sessions.calls), 1)
        self.assertIn("too quickly", second.channel.sent[0])

    async def test_one_user_does_not_consume_another_users_budget(self):
        sessions = FakeSessions()
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123, 456]),
            conversation_sessions=sessions,
            rate_limiter=DiscordRateLimiter(limit=1, window_seconds=60),
        )

        await handler.handle_message(Message(user_id=123))
        result = await handler.handle_message(Message(user_id=456))

        self.assertEqual(result.outcome, DiscordDMOutcome.REPLIED)
        self.assertEqual(len(sessions.calls), 2)

    async def test_reply_delivery_failure_does_not_escape_gateway_handler(self):
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123]),
            conversation_sessions=FakeSessions(),
        )

        result = await handler.handle_message(
            Message(channel=Channel(fail=True))
        )

        self.assertEqual(result.outcome, DiscordDMOutcome.FAILED)
        self.assertEqual(result.reply_parts, 0)


if __name__ == "__main__":
    unittest.main()
