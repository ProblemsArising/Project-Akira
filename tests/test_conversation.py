from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from app.conversation import ConversationResult, ConversationService


class FakeHistoryStore:
    def __init__(self):
        self.created = []
        self.turns = []
        self.next_id = 1

    def create_conversation(self, title=None):
        conversation_id = self.next_id
        self.next_id += 1
        self.created.append((conversation_id, title))
        return conversation_id

    def record_turn(self, **values):
        conversation_id = values["conversation_id"]
        if conversation_id is None:
            conversation_id = self.create_conversation(values.get("title"))
        self.turns.append({**values, "conversation_id": conversation_id})
        return conversation_id, len(self.turns)


class ConversationServiceTests(unittest.TestCase):
    def make_service(self, **overrides):
        calls = {
            "recorded": [],
            "transcribed": [],
            "responded": [],
            "spoken": [],
            "printed": [],
            "results": [],
        }

        defaults = {
            "recorder": lambda: "input.wav",
            "transcriber": lambda path: calls["transcribed"].append(path) or "hello",
            "responder": lambda text: calls["responded"].append(text) or "Hi there",
            "speaker": lambda reply: calls["spoken"].append(reply),
            "on_user_text": lambda text: calls["recorded"].append(text),
            "on_reply": lambda reply: calls["printed"].append(reply),
            "on_result": lambda result: calls["results"].append(result),
        }
        defaults.update(overrides)
        return ConversationService(**defaults), calls

    def test_process_text_returns_completed_result_and_speaks(self):
        service, calls = self.make_service()

        result = service.process_text("  hello Akira  ")

        self.assertEqual(
            result,
            ConversationResult(
                user_text="hello Akira",
                reply="Hi there",
                source="text",
                audio_file=None,
                spoken=True,
            ),
        )
        self.assertEqual(calls["recorded"], ["hello Akira"])
        self.assertEqual(calls["responded"], ["hello Akira"])
        self.assertEqual(calls["printed"], ["Hi there"])
        self.assertEqual(calls["spoken"], ["Hi there"])
        self.assertEqual(calls["results"], [result])

    def test_process_text_can_skip_speech_output(self):
        service, calls = self.make_service()

        result = service.process_text("typed message", speak=False)

        self.assertIsNotNone(result)
        self.assertFalse(result.spoken)
        self.assertEqual(calls["spoken"], [])

    def test_blank_text_is_ignored(self):
        service, calls = self.make_service()

        result = service.process_text("   ")

        self.assertIsNone(result)
        self.assertEqual(calls["responded"], [])
        self.assertEqual(calls["spoken"], [])

    def test_process_voice_once_passes_recorded_filename_to_transcriber(self):
        service, calls = self.make_service(recorder=lambda: "voice-test.wav")

        result = service.process_voice_once()

        self.assertIsNotNone(result)
        self.assertEqual(result.source, "voice")
        self.assertEqual(result.audio_file, "voice-test.wav")
        self.assertEqual(calls["transcribed"], ["voice-test.wav"])

    def test_voice_turn_stops_when_recording_returns_none(self):
        service, calls = self.make_service(recorder=lambda: None)

        result = service.process_voice_once()

        self.assertIsNone(result)
        self.assertEqual(calls["transcribed"], [])
        self.assertEqual(calls["responded"], [])

    def test_run_voice_loop_can_be_requested_to_stop(self):
        service = None
        record_count = 0

        def recorder():
            nonlocal record_count
            record_count += 1
            if record_count == 1:
                return "input.wav"
            service.request_stop()
            return None

        service, _ = self.make_service(recorder=recorder)
        service.run_voice_loop()

        self.assertFalse(service.is_running)
        self.assertTrue(service.stop_requested)
        self.assertEqual(record_count, 2)

    def test_second_voice_loop_cannot_start_while_running(self):
        entered = threading.Event()
        release = threading.Event()

        def recorder():
            entered.set()
            release.wait(timeout=2)
            return None

        service, _ = self.make_service(recorder=recorder)
        thread = threading.Thread(target=service.run_voice_loop)
        thread.start()
        self.assertTrue(entered.wait(timeout=1))

        with self.assertRaises(RuntimeError):
            service.run_voice_loop()

        service.request_stop()
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_successful_turn_is_saved_to_history(self):
        history = FakeHistoryStore()
        service, _ = self.make_service(history_store=history)

        result = service.process_text("Remember this", speak=False)

        self.assertIsNotNone(result)
        self.assertEqual(service.current_conversation_id, 1)
        self.assertEqual(len(history.turns), 1)
        self.assertEqual(history.turns[0]["user_text"], "Remember this")
        self.assertEqual(history.turns[0]["assistant_text"], "Hi there")
        self.assertEqual(history.turns[0]["source"], "text")
        self.assertFalse(history.turns[0]["spoken"])

    def test_voice_and_text_turns_share_current_history_conversation(self):
        history = FakeHistoryStore()
        service, _ = self.make_service(history_store=history)

        service.process_text("Typed first", speak=False)
        service.process_voice_once()

        self.assertEqual(len(history.turns), 2)
        self.assertEqual(history.turns[0]["conversation_id"], 1)
        self.assertEqual(history.turns[1]["conversation_id"], 1)
        self.assertEqual(history.turns[1]["source"], "voice")

    def test_start_new_conversation_changes_history_id(self):
        history = FakeHistoryStore()
        service, _ = self.make_service(history_store=history)

        service.process_text("First", speak=False)
        second_id = service.start_new_conversation("Second chat")
        service.process_text("Second", speak=False)

        self.assertEqual(second_id, 2)
        self.assertEqual(history.created[-1], (2, "Second chat"))
        self.assertEqual(history.turns[-1]["conversation_id"], 2)

    def test_history_can_be_disabled(self):
        service, _ = self.make_service(history_store=None)

        result = service.process_text("No database", speak=False)

        self.assertIsNotNone(result)
        self.assertIsNone(service.current_conversation_id)

    def test_history_failure_does_not_lose_completed_reply(self):
        class BrokenHistoryStore(FakeHistoryStore):
            def record_turn(self, **values):
                raise RuntimeError("database unavailable")

        errors = []
        service, calls = self.make_service(
            history_store=BrokenHistoryStore(),
            on_history_error=errors.append,
        )

        result = service.process_text("Still reply", speak=False)

        self.assertIsNotNone(result)
        self.assertEqual(result.reply, "Hi there")
        self.assertEqual(calls["printed"], ["Hi there"])
        self.assertEqual(len(errors), 1)
        self.assertEqual(str(errors[0]), "database unavailable")


    def test_text_factory_uses_noop_voice_components_when_speech_disabled(self):
        with mock.patch.dict(
            "sys.modules",
            {
                "ai.llm": mock.Mock(ask_ai=lambda text: "Reply"),
            },
        ), mock.patch.object(
            ConversationService,
            "_add_default_history",
        ):
            service = ConversationService.from_text_components(
                enable_speech=False,
                history_store=None,
                on_reply=None,
            )

        self.assertIsNone(service.process_voice_once())
        result = service.process_text("Hello", speak=False)
        self.assertIsNotNone(result)
        self.assertEqual(result.reply, "Reply")



    def test_start_and_stop_background_listening_interrupts_recorder(self):
        entered = threading.Event()
        recorder_stop = threading.Event()
        states = []
        reset_calls = []

        def recorder():
            entered.set()
            recorder_stop.wait(timeout=2)
            return None

        def reset_recorder():
            reset_calls.append(True)
            recorder_stop.clear()

        service, _ = self.make_service(
            recorder=recorder,
            stop_recorder=recorder_stop.set,
            reset_recorder=reset_recorder,
            on_listening_changed=states.append,
        )

        self.assertTrue(service.start_listening())
        self.assertTrue(entered.wait(timeout=1))
        self.assertTrue(service.is_listening)
        self.assertTrue(service.stop_listening(wait=True, timeout=2))

        self.assertFalse(service.is_listening)
        self.assertEqual(reset_calls, [True])
        self.assertEqual(states, [True, False])

    def test_start_listening_returns_false_when_already_active(self):
        entered = threading.Event()
        release = threading.Event()

        def recorder():
            entered.set()
            release.wait(timeout=2)
            return None

        service, _ = self.make_service(
            recorder=recorder,
            stop_recorder=release.set,
        )

        self.assertTrue(service.start_listening())
        self.assertTrue(entered.wait(timeout=1))
        self.assertFalse(service.start_listening())
        service.stop_listening(wait=True, timeout=2)

    def test_background_listening_can_restart_after_stop(self):
        entered_count = 0
        entered = threading.Event()
        recorder_stop = threading.Event()
        reset_count = 0

        def recorder():
            nonlocal entered_count
            entered_count += 1
            entered.set()
            recorder_stop.wait(timeout=2)
            return None

        def reset_recorder():
            nonlocal reset_count
            reset_count += 1
            recorder_stop.clear()

        service, _ = self.make_service(
            recorder=recorder,
            stop_recorder=recorder_stop.set,
            reset_recorder=reset_recorder,
        )

        self.assertTrue(service.start_listening())
        self.assertTrue(entered.wait(timeout=1))
        service.stop_listening(wait=True, timeout=2)

        entered.clear()
        self.assertTrue(service.start_listening())
        self.assertTrue(entered.wait(timeout=1))
        service.stop_listening(wait=True, timeout=2)

        self.assertGreaterEqual(entered_count, 2)
        self.assertEqual(reset_count, 2)

    def test_request_stop_interrupts_active_recorder(self):
        entered = threading.Event()
        recorder_stop = threading.Event()

        def recorder():
            entered.set()
            recorder_stop.wait(timeout=2)
            return None

        service, _ = self.make_service(
            recorder=recorder,
            stop_recorder=recorder_stop.set,
        )

        service.start_listening()
        self.assertTrue(entered.wait(timeout=1))
        service.request_stop()
        self.assertTrue(recorder_stop.is_set())
        self.assertTrue(service.wait_until_stopped(timeout=2))

    def test_stop_listening_returns_false_when_already_stopped(self):
        service, _ = self.make_service()

        self.assertFalse(service.stop_listening())
        self.assertFalse(service.is_listening)



if __name__ == "__main__":
    unittest.main()
