from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import assistant
from app.conversation import ConversationResult


class AssistantCliTests(unittest.TestCase):
    @patch("assistant.ConversationService.from_default_components")
    def test_default_mode_runs_voice_loop(self, factory):
        service = Mock()
        factory.return_value = service

        exit_code = assistant.main([])

        self.assertEqual(exit_code, 0)
        service.run_voice_loop.assert_called_once_with()
        service.process_text.assert_not_called()

    @patch("assistant.ConversationService.from_text_components")
    def test_one_shot_message_uses_text_pipeline(self, factory):
        service = Mock()
        service.process_text.return_value = ConversationResult(
            user_text="Hello",
            reply="Hi",
            source="text",
            spoken=False,
        )
        factory.return_value = service

        exit_code = assistant.main(["--message", "Hello", "--no-speak"])

        self.assertEqual(exit_code, 0)
        factory.assert_called_once()
        self.assertFalse(factory.call_args.kwargs["enable_speech"])
        service.process_text.assert_called_once_with(
            "Hello",
            speak=False,
            source="text",
        )
        service.run_voice_loop.assert_not_called()

    @patch("assistant.TextInputSession")
    @patch("assistant.ConversationService.from_text_components")
    def test_text_mode_runs_interactive_session(self, factory, session_class):
        service = Mock()
        factory.return_value = service

        exit_code = assistant.main(["--text", "--no-speak"])

        self.assertEqual(exit_code, 0)
        factory.assert_called_once()
        self.assertFalse(factory.call_args.kwargs["enable_speech"])
        session_class.assert_called_once_with(service=service, speak=False)
        session_class.return_value.run.assert_called_once_with()
        service.run_voice_loop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
