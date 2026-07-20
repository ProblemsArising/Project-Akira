from __future__ import annotations

import threading
import unittest

from dataclasses import dataclass

from app.discord_access import DiscordAccessPolicy
from app.discord_dm import (
    DEFAULT_FAILURE_REPLY,
    DiscordDMHandler,
    DiscordDMOutcome,
    get_discord_dm_handler,
    split_discord_message,
)


@dataclass
class FakeResult:
    reply: str


class FakeConversationService:
    def __init__(self, reply="Hello from Akira", error=None):
        self.reply = reply
        self.error = error
        self.calls = []
        self.thread_ids = []

    def process_text(self, text, *, speak=True, source="text", audio_file=None):
        self.calls.append(
            {
                "text": text,
                "speak": speak,
                "source": source,
                "audio_file": audio_file,
            }
        )
        self.thread_ids.append(threading.get_ident())
        if self.error is not None:
            raise self.error
        if self.reply is None:
            return None
        return FakeResult(self.reply)


class FakeTyping:
    def __init__(self, channel):
        self.channel = channel

    async def __aenter__(self):
        self.channel.typing_entered += 1

    async def __aexit__(self, exc_type, exc, traceback):
        self.channel.typing_exited += 1


class FakeChannel:
    def __init__(self):
        self.sent = []
        self.typing_entered = 0
        self.typing_exited = 0

    def typing(self):
        return FakeTyping(self)

    async def send(self, content):
        self.sent.append(content)


class FakeAuthor:
    def __init__(self, user_id, *, bot=False):
        self.id = user_id
        self.bot = bot


class FakeMessage:
    def __init__(
        self,
        *,
        user_id=123,
        content="Hello",
        bot=False,
        guild=None,
        channel=None,
    ):
        self.author = FakeAuthor(user_id, bot=bot)
        self.content = content
        self.guild = guild
        self.channel = channel or FakeChannel()


class DiscordDMHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_dm_is_processed_without_local_speech(self):
        service = FakeConversationService()
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123]),
            service_factory=lambda: service,
        )
        message = FakeMessage(content="  Hello Akira  ")
        event_loop_thread = threading.get_ident()

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.REPLIED)
        self.assertEqual(message.channel.sent, ["Hello from Akira"])
        self.assertEqual(service.calls[0]["text"], "Hello Akira")
        self.assertFalse(service.calls[0]["speak"])
        self.assertEqual(service.calls[0]["source"], "text")
        self.assertNotEqual(service.thread_ids, [event_loop_thread])
        self.assertEqual(message.channel.typing_entered, 1)
        self.assertEqual(message.channel.typing_exited, 1)

    async def test_unauthorized_user_is_silently_denied(self):
        factories = []
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([999]),
            service_factory=lambda: factories.append(True)
            or FakeConversationService(),
        )
        message = FakeMessage(user_id=123)

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.DENIED)
        self.assertEqual(message.channel.sent, [])
        self.assertEqual(factories, [])

    async def test_server_bot_and_blank_messages_are_ignored(self):
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123]),
            service_factory=FakeConversationService,
        )

        cases = [
            (FakeMessage(guild=object()), DiscordDMOutcome.IGNORED_NON_DM),
            (FakeMessage(bot=True), DiscordDMOutcome.IGNORED_BOT),
            (FakeMessage(content="   "), DiscordDMOutcome.IGNORED_EMPTY),
        ]

        for message, expected in cases:
            result = await handler.handle_message(message)
            self.assertEqual(result.outcome, expected)
            self.assertEqual(message.channel.sent, [])

    async def test_long_reply_is_split_at_discord_limit(self):
        reply = ("word " * 900).strip()
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123]),
            service_factory=lambda: FakeConversationService(reply=reply),
        )
        message = FakeMessage()

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.REPLIED)
        self.assertGreater(result.reply_parts, 1)
        self.assertTrue(all(len(part) <= 2_000 for part in message.channel.sent))
        self.assertEqual(" ".join(message.channel.sent), reply)

    async def test_backend_failure_returns_generic_message(self):
        secret = "secret backend detail"
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123]),
            service_factory=lambda: FakeConversationService(
                error=RuntimeError(secret)
            ),
        )
        message = FakeMessage()

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.FAILED)
        self.assertEqual(message.channel.sent, [DEFAULT_FAILURE_REPLY])
        self.assertNotIn(secret, message.channel.sent[0])

    async def test_empty_model_reply_returns_generic_message(self):
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([123]),
            service_factory=lambda: FakeConversationService(reply=None),
        )
        message = FakeMessage()

        result = await handler.handle_message(message)

        self.assertEqual(result.outcome, DiscordDMOutcome.REPLIED)
        self.assertEqual(message.channel.sent, [DEFAULT_FAILURE_REPLY])

    def test_splitter_validates_limit_and_preserves_short_text(self):
        self.assertEqual(split_discord_message(" hello "), ("hello",))
        self.assertEqual(split_discord_message("   "), ())
        with self.assertRaises(ValueError):
            split_discord_message("hello", limit=0)

    def test_default_handler_is_process_wide_singleton(self):
        self.assertIs(get_discord_dm_handler(), get_discord_dm_handler())


if __name__ == "__main__":
    unittest.main()
