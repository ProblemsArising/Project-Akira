from __future__ import annotations

import unittest
from dataclasses import dataclass

from app.conversation import ConversationService


@dataclass
class Summary:
    id: int
    title: str


@dataclass
class Turn:
    user_text: str
    assistant_text: str


class ResumeHistory:
    def __init__(self):
        self.summary = Summary(7, "Saved project chat")
        self.turns = [
            Turn("First question", "First answer"),
            Turn("Second question", "Second answer"),
        ]

    def get_conversation(self, conversation_id):
        return self.summary if int(conversation_id) == 7 else None

    def get_turns(self, conversation_id, *, limit=None):
        return list(self.turns) if int(conversation_id) == 7 else []

    def create_conversation(self, title=None):
        return 8

    def record_turn(self, **values):
        return values.get("conversation_id") or 8, 1


class ConversationResumeTests(unittest.TestCase):
    def make_service(self):
        loaded = []
        reset = []
        history = ResumeHistory()
        service = ConversationService(
            recorder=lambda: None,
            transcriber=lambda path: "",
            responder=lambda text: "reply",
            speaker=lambda text: None,
            history_store=history,
            load_conversation_context=lambda turns: loaded.append(turns),
            reset_conversation_context=lambda: reset.append(True),
        )
        return service, loaded, reset

    def test_activate_restores_context_and_selects_history_id(self):
        service, loaded, _ = self.make_service()
        events = []
        service.add_event_listener(
            lambda event_type, data: events.append((event_type, data))
        )

        selected = service.activate_conversation(7)

        self.assertEqual(selected, 7)
        self.assertEqual(service.current_conversation_id, 7)
        self.assertEqual(
            loaded,
            [[
                ("First question", "First answer"),
                ("Second question", "Second answer"),
            ]],
        )
        self.assertEqual(events[-1][0], "conversation.changed")
        self.assertTrue(events[-1][1]["resumed"])

    def test_new_conversation_resets_previous_short_term_context(self):
        service, _, reset = self.make_service()

        service.start_new_conversation("Fresh chat")

        self.assertEqual(reset, [True])

    def test_missing_saved_conversation_is_rejected(self):
        service, loaded, _ = self.make_service()

        with self.assertRaises(KeyError):
            service.activate_conversation(999)

        self.assertEqual(loaded, [])


if __name__ == "__main__":
    unittest.main()
