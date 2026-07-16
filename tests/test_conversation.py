from __future__ import annotations

import threading
import time
import unittest

from app.conversation import ConversationResult, ConversationService


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


if __name__ == "__main__":
    unittest.main()
