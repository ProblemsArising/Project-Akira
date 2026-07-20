from __future__ import annotations

import unittest

from dataclasses import dataclass

from app.discord_access import DiscordAccessPolicy
from app.discord_conversations import DiscordConversationSessions
from app.discord_dm import DiscordDMHandler, DiscordDMOutcome


@dataclass
class Result:
    reply: str


class Service:
    def __init__(self, user_id):
        self.user_id = user_id
        self.current_conversation_id = user_id
        self.messages = []

    def activate_conversation(self, conversation_id):
        self.current_conversation_id = int(conversation_id)
        return self.current_conversation_id

    def start_new_conversation(self, title=None):
        return self.current_conversation_id

    def process_text(self, text, *, speak=True, source="text", audio_file=None):
        self.messages.append(text)
        return Result(f"user {self.user_id}: {text}")


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


class Author:
    bot = False

    def __init__(self, user_id):
        self.id = user_id


class Message:
    guild = None

    def __init__(self, user_id, content):
        self.author = Author(user_id)
        self.channel = Channel()
        self.content = content


class DiscordDMConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_different_users_are_routed_to_different_sessions(self):
        services = {}

        def factory(user_id):
            service = Service(user_id)
            services[user_id] = service
            return service

        sessions = DiscordConversationSessions(service_factory=factory)
        handler = DiscordDMHandler(
            access_policy=DiscordAccessPolicy([100, 200]),
            conversation_sessions=sessions,
        )

        first = Message(100, "first")
        second = Message(200, "second")
        follow_up = Message(100, "follow up")

        self.assertEqual(
            (await handler.handle_message(first)).outcome,
            DiscordDMOutcome.REPLIED,
        )
        self.assertEqual(
            (await handler.handle_message(second)).outcome,
            DiscordDMOutcome.REPLIED,
        )
        self.assertEqual(
            (await handler.handle_message(follow_up)).outcome,
            DiscordDMOutcome.REPLIED,
        )

        self.assertEqual(services[100].messages, ["first", "follow up"])
        self.assertEqual(services[200].messages, ["second"])
        self.assertEqual(first.channel.sent, ["user 100: first"])
        self.assertEqual(second.channel.sent, ["user 200: second"])


if __name__ == "__main__":
    unittest.main()
