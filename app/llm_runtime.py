"""Runtime cleanup helpers for switching Project Akira LLM backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LLMRuntimeCleanup:
    context_reset: bool
    discord_sessions_reset: int
    managed_processes_stopped: int


def retire_llm_runtime_contexts(
    *,
    timeout: float = 5.0,
) -> LLMRuntimeCleanup:
    """Retire cached and per-user backends before using new settings."""

    from ai.llm import invalidate_default_llm
    from ai.llama_cpp_backend import stop_all_managed_llama_cpp_processes
    from app.discord_conversations import reset_discord_conversation_sessions

    context_reset = invalidate_default_llm()
    discord_sessions_reset = reset_discord_conversation_sessions()
    managed_processes_stopped = stop_all_managed_llama_cpp_processes(
        timeout=timeout,
    )
    return LLMRuntimeCleanup(
        context_reset=context_reset,
        discord_sessions_reset=discord_sessions_reset,
        managed_processes_stopped=managed_processes_stopped,
    )
