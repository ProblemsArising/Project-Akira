from __future__ import annotations

import types
import unittest
from unittest import mock

from ai.llm import create_llm_backend
from ai.llm_backend import LLMBackend
from ai.lm_studio_backend import LMStudioBackend
from config.settings import AppSettings


class NativeResponse:
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class LMStudioBackendTests(unittest.TestCase):
    def test_backend_metadata_and_factory_dispatch(self) -> None:
        settings = AppSettings()
        settings.llm.backend = "lm_studio"
        native_client = mock.Mock()

        backend = create_llm_backend(
            settings,
            native_client=native_client,
        )
        try:
            self.assertIsInstance(backend, LMStudioBackend)
            self.assertIsInstance(backend, LLMBackend)
            self.assertEqual(backend.info.backend_id, "lm_studio")
            self.assertEqual(backend.info.display_name, "LM Studio")
            self.assertFalse(backend.info.managed)
        finally:
            backend.close()

    def test_adapter_rejects_non_lm_studio_settings(self) -> None:
        settings = AppSettings()
        settings.llm.backend = "openai_compatible"

        with self.assertRaisesRegex(ValueError, "requires llm.backend"):
            LMStudioBackend(settings, client=object())

    def test_explicit_reasoning_uses_native_v1_chat(self) -> None:
        settings = AppSettings()
        settings.llm.backend = "lm_studio"
        settings.llm.base_url = "http://localhost:1234/v1"
        settings.llm.model = "local-model"
        settings.llm.api_key = "secret-token"
        settings.llm.reasoning_mode = "off"
        settings.llm.max_tokens = 512

        native_client = mock.Mock()
        native_client.post.return_value = NativeResponse(
            {
                "output": [
                    {"type": "message", "content": "Native reply"},
                ],
                "stats": {"total_output_tokens": 12},
            }
        )

        backend = LMStudioBackend(settings, native_client=native_client)
        try:
            with mock.patch("ai.llm.build_memory_context", return_value=""), mock.patch(
                "ai.llm.remember_turn"
            ):
                reply = backend.ask("Hello")

            self.assertEqual(reply, "Native reply")
            self.assertEqual(backend.api_mode, "native_v1")
            self.assertEqual(
                backend.native_chat_url,
                "http://localhost:1234/api/v1/chat",
            )
            call = native_client.post.call_args
            self.assertEqual(call.args[0], backend.native_chat_url)
            self.assertEqual(call.kwargs["json"]["model"], "local-model")
            self.assertEqual(call.kwargs["json"]["reasoning"], "off")
            self.assertEqual(call.kwargs["json"]["max_output_tokens"], 512)
            self.assertEqual(
                call.kwargs["headers"]["Authorization"],
                "Bearer secret-token",
            )
        finally:
            backend.close()

    def test_auto_reasoning_keeps_openai_compatible_endpoint(self) -> None:
        settings = AppSettings()
        settings.llm.backend = "lm_studio"
        settings.llm.reasoning_mode = "auto"

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
            ),
            close=mock.Mock(),
        )

        backend = LMStudioBackend(settings, client=client)
        try:
            with mock.patch("ai.llm.build_memory_context", return_value=""), mock.patch(
                "ai.llm.remember_turn"
            ):
                reply = backend.ask("Hello")

            self.assertEqual(reply, "Compatible reply")
            self.assertEqual(backend.api_mode, "openai_compatible")
            create.assert_called_once()
        finally:
            backend.close()

    def test_factory_preserves_generic_openai_compatible_backend(self) -> None:
        settings = AppSettings()
        settings.llm.backend = "openai_compatible"
        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=mock.Mock())
            )
        )

        backend = create_llm_backend(settings, client=client)
        try:
            self.assertNotIsInstance(backend, LMStudioBackend)
            self.assertEqual(backend.info.backend_id, "openai_compatible")
        finally:
            backend.close()

    def test_factory_rejects_unknown_backend(self) -> None:
        settings = AppSettings()
        settings.llm.backend = "not-real"

        with self.assertRaisesRegex(ValueError, "Unsupported LLM backend"):
            create_llm_backend(settings, client=object())


if __name__ == "__main__":
    unittest.main()
