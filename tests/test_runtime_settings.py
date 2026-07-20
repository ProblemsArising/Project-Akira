from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from config.settings import AppSettings, get_settings, update_settings


class RuntimeSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.settings_file = Path(self.temp_directory.name) / "settings.json"
        self.environment = mock.patch.dict(
            os.environ,
            {"AKIRA_SETTINGS_FILE": str(self.settings_file)},
        )
        self.environment.start()
        get_settings(reload=True)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_custom_personality_prompt_is_used(self) -> None:
        from ai.personality import get_personality

        settings = AppSettings()
        settings.personality.prompt = "You are a test personality."
        self.assertEqual(get_personality(settings), "You are a test personality.")

    def test_llm_uses_settings_and_retries_empty_reasoning_response(self) -> None:
        from ai.llm import LocalLLM

        first = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content="",
                        reasoning_content="thinking",
                    ),
                    finish_reason="length",
                )
            ]
        )
        second = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content="Visible reply",
                        reasoning_content="thinking",
                    ),
                    finish_reason="stop",
                )
            ]
        )
        create = mock.Mock(side_effect=[first, second])
        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create)
            )
        )
        settings = AppSettings()
        settings.llm.model = "configured-model"
        settings.llm.temperature = 0.4
        settings.llm.top_p = 0.8
        settings.llm.max_tokens = 100
        settings.llm.empty_response_retries = 1
        settings.llm.retry_token_multiplier = 2.0
        settings.llm.max_retry_tokens = 300
        settings.llm.stop_sequences = ["STOP"]

        with mock.patch("ai.llm.build_memory_context", return_value=""), mock.patch(
            "ai.llm.remember_turn"
        ) as remember:
            reply = LocalLLM(settings, client=client).ask("Hello")

        self.assertEqual(reply, "Visible reply")
        self.assertEqual(create.call_count, 2)
        self.assertEqual(create.call_args_list[0].kwargs["max_tokens"], 100)
        self.assertEqual(create.call_args_list[1].kwargs["max_tokens"], 200)
        self.assertEqual(create.call_args_list[0].kwargs["model"], "configured-model")
        self.assertEqual(create.call_args_list[0].kwargs["temperature"], 0.4)
        self.assertEqual(create.call_args_list[0].kwargs["top_p"], 0.8)
        self.assertEqual(create.call_args_list[0].kwargs["stop"], ["STOP"])
        remember.assert_called_once_with("Hello", "Visible reply")

    def test_llm_raises_clear_error_after_empty_response(self) -> None:
        from ai.llm import EmptyModelResponseError, LocalLLM

        empty = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content=None,
                        reasoning_content="thinking",
                    ),
                    finish_reason="length",
                )
            ]
        )
        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=mock.Mock(return_value=empty))
            )
        )
        settings = AppSettings()
        settings.llm.retry_empty_response = False

        llm = LocalLLM(settings, client=client)
        with mock.patch("ai.llm.build_memory_context", return_value=""):
            with self.assertRaisesRegex(EmptyModelResponseError, "reasoning"):
                llm.ask("Hello")

        self.assertEqual(len(llm.messages), 1)

    def test_lm_studio_native_backend_enforces_reasoning_off(self) -> None:
        from ai.llm import LocalLLM

        class NativeResponse:
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "output": [
                        {"type": "message", "content": "Immediate reply"}
                    ],
                    "stats": {
                        "total_output_tokens": 12,
                        "reasoning_output_tokens": 0,
                    },
                }

        native_client = mock.Mock()
        native_client.post.return_value = NativeResponse()

        settings = AppSettings()
        settings.llm.backend = "lm_studio"
        settings.llm.base_url = "http://localhost:1234/v1"
        settings.llm.reasoning_mode = "off"
        settings.llm.max_tokens = 1024

        with mock.patch("ai.llm.build_memory_context", return_value=""), mock.patch(
            "ai.llm.remember_turn"
        ):
            reply = LocalLLM(
                settings,
                native_client=native_client,
            ).ask("Hello")

        self.assertEqual(reply, "Immediate reply")
        call = native_client.post.call_args
        self.assertEqual(call.args[0], "http://localhost:1234/api/v1/chat")
        self.assertEqual(call.kwargs["json"]["reasoning"], "off")
        self.assertEqual(call.kwargs["json"]["max_output_tokens"], 1024)
        self.assertFalse(call.kwargs["json"]["store"])
        self.assertIn("Hello", call.kwargs["json"]["input"])

    def test_auto_reasoning_keeps_openai_compatible_backend(self) -> None:
        from ai.llm import LocalLLM

        response = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content="Compatible reply",
                        reasoning_content=None,
                    ),
                    finish_reason="stop",
                )
            ]
        )
        create = mock.Mock(return_value=response)
        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create)
            )
        )

        settings = AppSettings()
        settings.llm.reasoning_mode = "auto"

        with mock.patch("ai.llm.build_memory_context", return_value=""), mock.patch(
            "ai.llm.remember_turn"
        ):
            reply = LocalLLM(settings, client=client).ask("Hello")

        self.assertEqual(reply, "Compatible reply")
        create.assert_called_once()

    def test_whisper_model_and_transcription_options_come_from_settings(self) -> None:
        from audio.whisper_stt import WhisperTranscriber

        model = mock.Mock()
        model.transcribe.return_value = (
            [types.SimpleNamespace(text=" Hello "), types.SimpleNamespace(text="there")],
            object(),
        )
        factory = mock.Mock(return_value=model)
        settings = AppSettings()
        settings.stt.model = "small"
        settings.stt.device = "cpu"
        settings.stt.compute_type = "int8"
        settings.stt.language = "en"
        settings.stt.beam_size = 3

        transcriber = WhisperTranscriber(settings, model_factory=factory)
        result = transcriber.transcribe("speech.wav")

        self.assertEqual(result, "Hello there")
        factory.assert_called_once_with("small", device="cpu", compute_type="int8")
        model.transcribe.assert_called_once_with("speech.wav", beam_size=3, language="en")

    def test_memory_path_and_limits_come_from_settings(self) -> None:
        from ai import memory

        memory_file = Path(self.temp_directory.name) / "custom-memory.json"
        update_settings(
            {
                "memory": {
                    "file": str(memory_file),
                    "max_turns": 1,
                    "max_facts": 1,
                }
            }
        )

        with mock.patch.object(memory, "_extract_fact_candidates", return_value=[]):
            memory.remember_turn("one", "first")
            memory.remember_turn("two", "second")

        saved = json.loads(memory_file.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["turns"]), 1)
        self.assertEqual(saved["turns"][0]["user"], "two")

    def test_default_component_factory_passes_audio_and_tts_settings(self) -> None:
        from app.conversation import ConversationService

        update_settings(
            {
                "audio": {
                    "recording_file": "configured.wav",
                    "sample_rate": 22050,
                    "channels": 1,
                    "frame_ms": 20,
                    "end_silence_seconds": 1.25,
                },
                "tts": {"voice_index": 0, "rate": 160, "volume": 0.7},
                "avatar": {"mouth_end_delay_seconds": 0.2},
            }
        )

        recorder = mock.Mock()
        recorder.record = mock.Mock()
        recorder.request_stop = mock.Mock()
        recorder.reset = mock.Mock()
        recorder_type = mock.Mock(return_value=recorder)
        speaker = mock.Mock()
        create_speaker = mock.Mock(return_value=speaker)

        fake_modules = {
            "ai.llm": types.SimpleNamespace(ask_ai=lambda text: "reply"),
            "audio.devices": types.SimpleNamespace(resolve_audio_device=lambda value, kind: None),
            "audio.microphone": types.SimpleNamespace(MicrophoneRecorder=recorder_type),
            "audio.tts": types.SimpleNamespace(create_speaker=create_speaker),
            "audio.whisper_stt": types.SimpleNamespace(transcribe=lambda path: "text"),
        }

        with mock.patch.dict("sys.modules", fake_modules), mock.patch.object(
            ConversationService, "_add_default_history"
        ):
            service = ConversationService.from_default_components(history_store=None)

        self.assertIs(service._speaker, speaker)
        kwargs = recorder_type.call_args.kwargs
        expected_recording_file = (
            Path(__file__).resolve().parents[1] / "configured.wav"
        ).resolve()
        self.assertEqual(kwargs["output_file"], expected_recording_file)
        self.assertEqual(kwargs["sample_rate"], 22050)
        self.assertEqual(kwargs["frame_ms"], 20)
        self.assertEqual(kwargs["end_silence_seconds"], 1.25)
        create_speaker.assert_called_once_with(
            output_device=None,
            voice_index=0,
            rate=160,
            volume=0.7,
            mouth_end_delay_seconds=0.2,
        )


if __name__ == "__main__":
    unittest.main()
