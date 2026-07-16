from __future__ import annotations

import unittest

from app.conversation import ConversationResult
from app.text_input import TextInputSession


class FakeConversationService:
    def __init__(self) -> None:
        self.messages = []
        self.new_conversations = []
        self.next_conversation_id = 1

    def process_text(self, text, *, speak=True, source="text", audio_file=None):
        self.messages.append(
            {
                "text": text,
                "speak": speak,
                "source": source,
                "audio_file": audio_file,
            }
        )
        return ConversationResult(
            user_text=text,
            reply="Reply",
            source="text",
            spoken=speak,
        )

    def start_new_conversation(self, title=None):
        conversation_id = self.next_conversation_id
        self.next_conversation_id += 1
        self.new_conversations.append((conversation_id, title))
        return conversation_id


class TextInputSessionTests(unittest.TestCase):
    def test_submit_uses_text_source_and_speech_preference(self):
        service = FakeConversationService()
        session = TextInputSession(service=service, speak=False)

        result = session.submit("Hello Akira")

        self.assertIsNotNone(result)
        self.assertEqual(
            service.messages,
            [
                {
                    "text": "Hello Akira",
                    "speak": False,
                    "source": "text",
                    "audio_file": None,
                }
            ],
        )

    def test_run_processes_messages_until_quit(self):
        service = FakeConversationService()
        values = iter(["", "Hello", "/quit"])
        output = []
        session = TextInputSession(
            service=service,
            input_function=lambda prompt: next(values),
            output_function=output.append,
        )

        session.run()

        self.assertEqual(len(service.messages), 1)
        self.assertEqual(service.messages[0]["text"], "Hello")
        self.assertIn("Stopping text chat...", output)

    def test_speak_command_changes_later_messages(self):
        service = FakeConversationService()
        values = iter(["/speak off", "Quiet reply", "/speak on", "Spoken reply", "/quit"])
        session = TextInputSession(
            service=service,
            input_function=lambda prompt: next(values),
            output_function=lambda message: None,
        )

        session.run()

        self.assertFalse(service.messages[0]["speak"])
        self.assertTrue(service.messages[1]["speak"])

    def test_new_command_starts_named_conversation(self):
        service = FakeConversationService()
        values = iter(["/new Minecraft planning", "/quit"])
        output = []
        session = TextInputSession(
            service=service,
            input_function=lambda prompt: next(values),
            output_function=output.append,
        )

        session.run()

        self.assertEqual(service.new_conversations, [(1, "Minecraft planning")])
        self.assertIn("Started conversation #1.", output)

    def test_unknown_command_does_not_submit_message(self):
        service = FakeConversationService()
        values = iter(["/dance", "/quit"])
        output = []
        session = TextInputSession(
            service=service,
            input_function=lambda prompt: next(values),
            output_function=output.append,
        )

        session.run()

        self.assertEqual(service.messages, [])
        self.assertTrue(any("Unknown command" in message for message in output))

    def test_eof_stops_session_cleanly(self):
        service = FakeConversationService()
        output = []

        def closed_input(prompt):
            raise EOFError

        session = TextInputSession(
            service=service,
            input_function=closed_input,
            output_function=output.append,
        )

        session.run()

        self.assertEqual(service.messages, [])
        self.assertTrue(any("Text input closed" in message for message in output))


if __name__ == "__main__":
    unittest.main()
