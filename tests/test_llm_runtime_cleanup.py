from __future__ import annotations

import unittest
from unittest import mock

from app.discord_conversations import (
    DiscordConversationSessions,
    InMemoryDiscordConversationMap,
)
from app.llm_runtime import retire_llm_runtime_contexts


class FakeService:
    current_conversation_id = None

    def activate_conversation(self, conversation_id):
        self.current_conversation_id = int(conversation_id)
        return self.current_conversation_id

    def start_new_conversation(self, title=None):
        return None

    def process_text(self, text, *, speak=True, source="text", audio_file=None):
        return None


class LLMRuntimeCleanupTests(unittest.TestCase):
    def test_discord_session_reset_preserves_saved_mapping_store(self):
        mapping = InMemoryDiscordConversationMap()
        sessions = DiscordConversationSessions(
            service_factory=lambda _user_id: FakeService(),
            conversation_map=mapping,
        )
        sessions.service_for(101)
        sessions.service_for(202)

        self.assertEqual(sessions.reset_runtime_sessions(), 2)
        self.assertEqual(sessions.snapshot().active_session_count, 0)

    def test_retire_closes_default_discord_and_managed_contexts(self):
        with mock.patch(
            "ai.llm.invalidate_default_llm",
            return_value=True,
        ) as invalidate, mock.patch(
            "app.discord_conversations.reset_discord_conversation_sessions",
            return_value=2,
        ) as reset_discord, mock.patch(
            "ai.llama_cpp_backend.stop_all_managed_llama_cpp_processes",
            return_value=1,
        ) as stop_managed:
            result = retire_llm_runtime_contexts(timeout=3.0)

        invalidate.assert_called_once_with()
        reset_discord.assert_called_once_with()
        stop_managed.assert_called_once_with(timeout=3.0)
        self.assertTrue(result.context_reset)
        self.assertEqual(result.discord_sessions_reset, 2)
        self.assertEqual(result.managed_processes_stopped, 1)


if __name__ == "__main__":
    unittest.main()
