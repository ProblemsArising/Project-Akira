from __future__ import annotations

import unittest

from app.conversation import ConversationService


class ConversationEventTests(unittest.TestCase):
    def make_service(self, *, recorder=lambda: None, transcriber=lambda _: ""):
        events: list[tuple[str, dict]] = []
        service = ConversationService(
            recorder=recorder,
            transcriber=transcriber,
            responder=lambda text: f"reply:{text}",
            speaker=lambda reply: None,
            on_reply=None,
            on_event=lambda event_type, data: events.append(
                (event_type, dict(data))
            ),
        )
        return service, events

    def test_text_turn_emits_progress_and_completion(self) -> None:
        service, events = self.make_service()
        result = service.process_text("Hello", speak=False)

        self.assertIsNotNone(result)
        self.assertEqual(
            [event_type for event_type, _ in events],
            ["chat.started", "chat.reply_ready", "chat.completed"],
        )
        self.assertEqual(events[-1][1]["reply"], "reply:Hello")
        self.assertFalse(events[-1][1]["spoken"])
        self.assertEqual(
            events[1][1]["expression"],
            {"preset": "soft", "score": 0},
        )

    def test_reply_ready_uses_shared_avatar_expression_classifier(self) -> None:
        events: list[tuple[str, dict]] = []
        service = ConversationService(
            recorder=lambda: None,
            transcriber=lambda _: "",
            responder=lambda _: "That is awesome and perfect!",
            speaker=lambda _: None,
            on_reply=None,
            on_event=lambda event_type, data: events.append(
                (event_type, dict(data))
            ),
        )

        service.process_text("Did it work?", speak=False)

        expression = events[1][1]["expression"]
        self.assertEqual(expression["preset"], "happy")
        self.assertGreaterEqual(expression["score"], 4)

    def test_voice_turn_emits_recording_and_transcription_events(self) -> None:
        service, events = self.make_service(
            recorder=lambda: "input.wav",
            transcriber=lambda _: "Voice hello",
        )
        result = service.process_voice_once()

        self.assertIsNotNone(result)
        event_types = [event_type for event_type, _ in events]
        self.assertEqual(
            event_types[:4],
            [
                "voice.recording.started",
                "voice.recording.completed",
                "voice.transcription.started",
                "voice.transcription.completed",
            ],
        )
        self.assertEqual(event_types[-1], "chat.completed")

    def test_listener_failure_does_not_break_conversation(self) -> None:
        service, events = self.make_service()

        def broken_listener(event_type: str, data: dict) -> None:
            raise RuntimeError("browser disconnected")

        service.add_event_listener(broken_listener)
        result = service.process_text("Still works", speak=False)
        self.assertEqual(result.reply, "reply:Still works")
        self.assertEqual(events[-1][0], "chat.completed")

    def test_listening_and_new_conversation_emit_state_events(self) -> None:
        service, events = self.make_service()
        service.start_new_conversation("No history")
        self.assertEqual(events[-1][0], "conversation.changed")

        # Recorder immediately returns None, so run the state callback directly
        # through a normal start/stop cycle without blocking the test.
        started = service.start_listening()
        self.assertTrue(started)
        service.stop_listening(wait=True, timeout=1.0)
        listening_events = [
            data
            for event_type, data in events
            if event_type == "listening.changed"
        ]
        self.assertTrue(listening_events)
        self.assertTrue(listening_events[0]["is_listening"])
        self.assertFalse(listening_events[-1]["is_listening"])


if __name__ == "__main__":
    unittest.main()
