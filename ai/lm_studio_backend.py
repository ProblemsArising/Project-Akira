"""Explicit LM Studio implementation of Project Akira's LLM backend.

LM Studio remains an external, user-managed inference server. Project Akira
supports both its OpenAI-compatible chat endpoint and the native v1 chat
endpoint used when a reasoning mode must be enforced.
"""

from __future__ import annotations

from typing import Any

from config.settings import AppSettings, get_settings

from .llm import LocalLLM
from .llm_backend import LLMBackendInfo, normalize_backend_id


class LMStudioBackend(LocalLLM):
    """Stateful Project Akira chat backend backed by LM Studio."""

    backend_id = "lm_studio"

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        client: Any | None = None,
        native_client: Any | None = None,
    ) -> None:
        resolved = settings or get_settings()
        configured = normalize_backend_id(resolved.llm.backend)
        if configured != self.backend_id:
            raise ValueError(
                "LMStudioBackend requires llm.backend='lm_studio'; "
                f"received {configured!r}."
            )
        super().__init__(
            resolved,
            client=client,
            native_client=native_client,
        )

    @property
    def info(self) -> LLMBackendInfo:
        """Describe LM Studio as an external, unmanaged backend."""

        return LLMBackendInfo(
            backend_id=self.backend_id,
            display_name="LM Studio",
            managed=False,
        )

    @property
    def api_mode(self) -> str:
        """Return the active LM Studio inference API mode."""

        if self._uses_native_lm_studio():
            return "native_v1"
        return "openai_compatible"

    @property
    def native_chat_url(self) -> str:
        """Expose the resolved native endpoint for status and diagnostics."""

        return self._native_chat_url()
