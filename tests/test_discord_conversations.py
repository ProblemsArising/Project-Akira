from __future__ import annotations

import json
import tempfile
import unittest

from dataclasses import dataclass
from pathlib import Path

from app.discord_conversations import (
    DiscordConversationMap,
    DiscordConversationSessions,
    InMemoryDiscordConversationMap,
)


@dataclass
class FakeResult:
    reply: str


class FakeHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.conversations = set()

    def get_conversation(self, conversation_id):
        return object() if int(conversation_id) in self.conversations else None


class FakeConversationService:
    def __init__(self, *, first_conversation_id=100) -> None:
        self.current_conversation_id = None
        self.first_conversation_id = first_conversation_id
        self.activated = []
        self.calls = []

    def activate_conversation(self, conversation_id):
        conversation_id = int(conversation_id)
        self.activated.append(conversation_id)
        self.current_conversation_id = conversation_id
        return conversation_id

    def start_new_conversation(self, title=None):
        self.current_conversation_id = self.first_conversation_id
        return self.current_conversation_id

    def process_text(self, text, *, speak=True, source="text", audio_file=None):
        self.calls.append((text, speak, source))
        if self.current_conversation_id is None:
            self.current_conversation_id = self.first_conversation_id
        return FakeResult(f"reply:{text}")


class DiscordConversationMapTests(unittest.TestCase):
    def test_mapping_persists_across_store_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = FakeHistoryStore(root / "chat_history.db")
            history.conversations.add(42)

            first = DiscordConversationMap(history)
            first.bind(123456789, 42)

            second = DiscordConversationMap(history)
            self.assertEqual(second.conversation_for("123456789"), 42)
            self.assertEqual(second.snapshot().mapped_user_count, 1)

            payload = json.loads(second.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["users"], {"123456789": 42})

    def test_deleted_conversation_cleans_stale_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            history = FakeHistoryStore(Path(directory) / "chat_history.db")
            history.conversations.add(8)
            mapping = DiscordConversationMap(history)
            mapping.bind(123, 8)

            history.conversations.remove(8)

            self.assertIsNone(mapping.conversation_for(123))
            self.assertEqual(mapping.snapshot().mapped_user_count, 0)

    def test_corrupt_mapping_is_backed_up_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = FakeHistoryStore(root / "chat_history.db")
            mapping = DiscordConversationMap(history)
            mapping.path.write_text("{broken", encoding="utf-8")

            self.assertIsNone(mapping.conversation_for(123))
            backups = list(root.glob("discord_conversations.broken-*.json"))
            self.assertEqual(len(backups), 1)


class DiscordConversationSessionsTests(unittest.TestCase):
    def test_each_user_receives_a_separate_reused_service(self):
        created = {}

        def factory(user_id):
            service = FakeConversationService(first_conversation_id=user_id)
            created[user_id] = service
            return service

        sessions = DiscordConversationSessions(service_factory=factory)

        first = sessions.service_for(100)
        second = sessions.service_for(200)

        self.assertIs(first, sessions.service_for(100))
        self.assertIs(second, sessions.service_for(200))
        self.assertIsNot(first, second)
        self.assertEqual(sessions.snapshot().active_session_count, 2)

    def test_saved_conversation_is_restored_for_new_runtime_session(self):
        mapping = InMemoryDiscordConversationMap()
        mapping.bind(123, 77)
        service = FakeConversationService()
        sessions = DiscordConversationSessions(
            service_factory=lambda user_id: service,
            conversation_map=mapping,
        )

        self.assertIs(sessions.service_for(123), service)
        self.assertEqual(service.activated, [77])

    def test_completed_turn_updates_mapping(self):
        mapping = InMemoryDiscordConversationMap()
        service = FakeConversationService(first_conversation_id=55)
        sessions = DiscordConversationSessions(
            service_factory=lambda user_id: service,
            conversation_map=mapping,
        )

        result = sessions.process_text(123, "hello")

        self.assertEqual(result.reply, "reply:hello")
        self.assertEqual(mapping.conversation_for(123), 55)
        self.assertEqual(service.calls, [("hello", False, "text")])

    def test_new_conversation_replaces_saved_mapping(self):
        mapping = InMemoryDiscordConversationMap()
        mapping.bind(123, 10)
        service = FakeConversationService(first_conversation_id=99)
        sessions = DiscordConversationSessions(
            service_factory=lambda user_id: service,
            conversation_map=mapping,
        )

        conversation_id = sessions.start_new_conversation(123, "Discord chat")

        self.assertEqual(conversation_id, 99)
        self.assertEqual(mapping.conversation_for(123), 99)

    def test_forgetting_runtime_session_preserves_saved_mapping(self):
        mapping = InMemoryDiscordConversationMap()
        service = FakeConversationService(first_conversation_id=25)
        sessions = DiscordConversationSessions(
            service_factory=lambda user_id: service,
            conversation_map=mapping,
        )
        sessions.process_text(123, "hello")

        self.assertTrue(sessions.forget_runtime_session(123))
        self.assertEqual(mapping.conversation_for(123), 25)
        self.assertEqual(sessions.snapshot().active_session_count, 0)


if __name__ == "__main__":
    unittest.main()
