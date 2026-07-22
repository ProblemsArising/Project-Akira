"""Common interface for Project Akira language-model backends.

The conversation layer should not need to know whether replies come from
LM Studio, a managed llama.cpp process, or a future local inference engine.
Backends expose one small stateful chat contract while transport, model
loading, and process management remain implementation details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol, runtime_checkable


LLMHistoryTurn = tuple[str, str]


@dataclass(frozen=True, slots=True)
class LLMBackendInfo:
    """Stable metadata describing one configured backend implementation."""

    backend_id: str
    display_name: str
    managed: bool = False


@runtime_checkable
class LLMBackend(Protocol):
    """Stateful conversational backend used by Project Akira."""

    @property
    def info(self) -> LLMBackendInfo:
        """Return stable backend metadata without starting inference."""

        ...

    def ask(self, prompt: str) -> str:
        """Generate Akira's reply for one user prompt."""

        ...

    def reset_short_term_memory(self) -> None:
        """Start a clean short-term conversation context."""

        ...

    def load_short_term_history(
        self,
        turns: Iterable[LLMHistoryTurn],
    ) -> None:
        """Replace short-term context with saved user/assistant turns."""

        ...

    def set_system_prompt(self, prompt: str) -> None:
        """Replace the active system/personality prompt in place."""

        ...

    def close(self) -> None:
        """Release backend-owned clients, processes, or other resources."""

        ...


LLMBackendFactory = Callable[[], LLMBackend]


def normalize_backend_id(value: object) -> str:
    """Return a stable settings/registry identifier for a backend name."""

    normalized = str(value or "").strip().casefold().replace("-", "_")
    normalized = "_".join(part for part in normalized.split() if part)
    return normalized or "unknown"


def backend_display_name(backend_id: object) -> str:
    """Return a readable name for built-in and third-party backend IDs."""

    normalized = normalize_backend_id(backend_id)
    known = {
        "lm_studio": "LM Studio",
        "openai_compatible": "OpenAI-compatible server",
        "llama_cpp": "Managed llama.cpp",
    }
    if normalized in known:
        return known[normalized]
    return normalized.replace("_", " ").title()
