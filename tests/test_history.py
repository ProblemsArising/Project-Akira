from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.history import ChatHistoryStore


class ChatHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_file = Path(self.temp_directory.name) / "history.db"
        self.store = ChatHistoryStore(self.database_file)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_database_and_schema_are_created(self) -> None:
        self.assertTrue(self.database_file.exists())
        self.assertEqual(self.store.count_conversations(), 0)

    def test_record_turn_creates_conversation_and_persists_metadata(self) -> None:
        conversation_id, turn_id = self.store.record_turn(
            conversation_id=None,
            user_text="Hello Akira",
            assistant_text="Hey there!",
            source="voice",
            audio_file="input.wav",
            spoken=True,
        )

        self.assertGreater(conversation_id, 0)
        self.assertGreater(turn_id, 0)

        conversations = self.store.list_conversations()
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0].title, "Hello Akira")
        self.assertEqual(conversations[0].turn_count, 1)
        self.assertEqual(conversations[0].last_message, "Hey there!")

        turns = self.store.get_turns(conversation_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].source, "voice")
        self.assertEqual(turns[0].audio_file, "input.wav")
        self.assertTrue(turns[0].spoken)

    def test_multiple_turns_can_share_one_conversation(self) -> None:
        conversation_id, _ = self.store.record_turn(
            conversation_id=None,
            user_text="First message",
            assistant_text="First reply",
            source="text",
        )
        returned_id, _ = self.store.record_turn(
            conversation_id=conversation_id,
            user_text="Second message",
            assistant_text="Second reply",
            source="voice",
            spoken=True,
        )

        self.assertEqual(returned_id, conversation_id)
        turns = self.store.get_turns(conversation_id)
        self.assertEqual([turn.user_text for turn in turns], ["First message", "Second message"])
        self.assertEqual(self.store.list_conversations()[0].turn_count, 2)

    def test_search_matches_user_and_assistant_text(self) -> None:
        conversation_id, _ = self.store.record_turn(
            conversation_id=None,
            user_text="I am building a Minecraft bot",
            assistant_text="That sounds fun",
            source="text",
        )
        self.store.record_turn(
            conversation_id=conversation_id,
            user_text="What should it collect?",
            assistant_text="Start with spruce wood",
            source="text",
        )

        user_matches = self.store.search_turns("minecraft")
        assistant_matches = self.store.search_turns("spruce")

        self.assertEqual(len(user_matches), 1)
        self.assertEqual(user_matches[0].user_text, "I am building a Minecraft bot")
        self.assertEqual(len(assistant_matches), 1)
        self.assertEqual(assistant_matches[0].assistant_text, "Start with spruce wood")

    def test_rename_and_delete_conversation(self) -> None:
        conversation_id, _ = self.store.record_turn(
            conversation_id=None,
            user_text="Original title",
            assistant_text="Reply",
            source="text",
        )

        self.store.rename_conversation(conversation_id, "Minecraft planning")
        self.assertEqual(self.store.list_conversations()[0].title, "Minecraft planning")

        self.assertTrue(self.store.delete_conversation(conversation_id))
        self.assertFalse(self.store.delete_conversation(conversation_id))
        self.assertEqual(self.store.count_conversations(), 0)

    def test_blank_turns_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.record_turn(
                conversation_id=None,
                user_text=" ",
                assistant_text="Reply",
                source="text",
            )

        with self.assertRaises(ValueError):
            self.store.record_turn(
                conversation_id=None,
                user_text="Question",
                assistant_text=" ",
                source="text",
            )


if __name__ == "__main__":
    unittest.main()
