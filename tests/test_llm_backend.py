from __future__ import annotations

import types
import unittest
from unittest import mock

from ai.llm_backend import (
    LLMBackend,
    LLMBackendInfo,
    backend_display_name,
    normalize_backend_id,
)
from app.conversation import ConversationService
from config.settings import AppSettings


class FakeBackend:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.loaded_turns: list[tuple[str, str]] = []
        self.reset_count = 0
        self.system_prompt = ""
        self.closed = False

    @property
    def info(self) -> LLMBackendInfo:
        return LLMBackendInfo(
            backend_id="fake",
            display_name="Fake backend",
            managed=True,
        )

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"reply:{prompt}"

    def reset_short_term_memory(self) -> None:
        self.reset_count += 1

    def load_short_term_history(self, turns) -> None:
        self.loaded_turns = list(turns)

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt

    def close(self) -> None:
        self.closed = True


class LLMBackendInterfaceTests(unittest.TestCase):
    def test_structural_backend_satisfies_runtime_protocol(self) -> None:
        backend = FakeBackend()

        self.assertIsInstance(backend, LLMBackend)
        self.assertEqual(backend.info.backend_id, "fake")
        self.assertTrue(backend.info.managed)

    def test_backend_identifiers_and_display_names_are_normalized(self) -> None:
        self.assertEqual(normalize_backend_id(" LM-Studio "), "lm_studio")
        self.assertEqual(
            backend_display_name("openai-compatible"),
            "OpenAI-compatible server",
        )
        self.assertEqual(backend_display_name("custom_server"), "Custom Server")

    def test_current_factory_returns_interface_compatible_backend(self) -> None:
        from ai.llm import create_llm_backend

        response = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="Hello", reasoning=None),
                    finish_reason="stop",
                )
            ]
        )
        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=mock.Mock(return_value=response)
                )
            )
        )
        settings = AppSettings()
        settings.llm.backend = "openai_compatible"

        backend = create_llm_backend(settings, client=client)
        try:
            self.assertIsInstance(backend, LLMBackend)
            self.assertEqual(backend.info.backend_id, "openai_compatible")
            self.assertEqual(
                backend.info.display_name,
                "OpenAI-compatible server",
            )
            self.assertFalse(backend.info.managed)
        finally:
            backend.close()

    def test_text_factory_accepts_backend_without_default_llm_import(self) -> None:
        backend = FakeBackend()

        with mock.patch.object(ConversationService, "_add_default_history"):
            service = ConversationService.from_text_components(
                enable_speech=False,
                llm_backend=backend,
                history_store=None,
                on_reply=None,
            )

        result = service.process_text("hello", speak=False)
        self.assertIsNotNone(result)
        self.assertEqual(result.reply, "reply:hello")
        self.assertEqual(backend.prompts, ["hello"])

        service._load_conversation_context([("old", "reply")])
        service._reset_conversation_context()
        self.assertEqual(backend.loaded_turns, [("old", "reply")])
        self.assertEqual(backend.reset_count, 1)


if __name__ == "__main__":
    unittest.main()
