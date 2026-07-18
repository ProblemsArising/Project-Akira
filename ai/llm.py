"""Configurable local LLM client used by Project Akira."""

from __future__ import annotations

import threading
from dataclasses import asdict
from typing import Any


from config.settings import AppSettings, get_settings
from .memory import build_memory_context, remember_turn
from .personality import get_personality


class EmptyModelResponseError(RuntimeError):
    """Raised when the model never produces visible assistant text."""


class LocalLLM:
    """OpenAI-compatible chat backend with Project Akira memory handling."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_settings = self.settings.llm
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=self.llm_settings.base_url,
                api_key=self.llm_settings.api_key or "None",
            )
        self.client = client
        self.messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": get_personality(self.settings),
            }
        ]
        self._lock = threading.RLock()

    def reset_short_term_memory(self) -> None:
        """Clear current-run messages while preserving the system prompt."""

        with self._lock:
            self.messages = [
                {
                    "role": "system",
                    "content": get_personality(self.settings),
                }
            ]

    def _trim_short_term_memory(self) -> None:
        system_message = self.messages[0]
        conversation = self.messages[1:]
        limit = max(0, int(self.llm_settings.max_short_term_messages))

        if limit == 0:
            conversation = []
        elif len(conversation) > limit:
            conversation = conversation[-limit:]

        self.messages = [system_message] + conversation

    def _api_messages(self, prompt: str) -> list[dict[str, str]]:
        memory_context = build_memory_context(prompt)
        api_messages = [self.messages[0]]

        if memory_context:
            api_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Use this long-term memory only when it is relevant. "
                        "Do not mention the memory system or the JSON file. "
                        "If the user asks what they said before, use the memory below.\n\n"
                        f"{memory_context}"
                    ),
                }
            )

        api_messages.extend(self.messages[1:])
        return api_messages

    def _create_completion(
        self,
        api_messages: list[dict[str, str]],
        *,
        max_tokens: int,
    ) -> Any:
        return self.client.chat.completions.create(
            model=self.llm_settings.model,
            messages=api_messages,
            temperature=float(self.llm_settings.temperature),
            top_p=float(self.llm_settings.top_p),
            max_tokens=max(1, int(max_tokens)),
            stop=list(self.llm_settings.stop_sequences),
        )

    @staticmethod
    def _response_details(response: Any) -> tuple[str, str | None, str | None]:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return "", None, None

        choice = choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
        reasoning = None
        if message is not None:
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning is None:
                reasoning = getattr(message, "reasoning", None)

        reply = str(content or "").strip()
        finish_reason = getattr(choice, "finish_reason", None)
        return reply, finish_reason, str(reasoning) if reasoning else None

    def _next_retry_budget(self, current: int) -> int:
        multiplier = max(1.0, float(self.llm_settings.retry_token_multiplier))
        maximum = max(current, int(self.llm_settings.max_retry_tokens))
        return min(maximum, max(current + 1, int(round(current * multiplier))))

    def ask(self, prompt: str) -> str:
        normalized = str(prompt).strip()
        if not normalized:
            return ""

        with self._lock:
            self.messages.append({"role": "user", "content": normalized})
            api_messages = self._api_messages(normalized)
            max_tokens = max(1, int(self.llm_settings.max_tokens))
            retries = (
                max(0, int(self.llm_settings.empty_response_retries))
                if self.llm_settings.retry_empty_response
                else 0
            )

            last_finish_reason: str | None = None
            last_had_reasoning = False

            try:
                for attempt in range(retries + 1):
                    response = self._create_completion(
                        api_messages,
                        max_tokens=max_tokens,
                    )
                    reply, finish_reason, reasoning = self._response_details(response)
                    last_finish_reason = finish_reason
                    last_had_reasoning = bool(reasoning)

                    if reply:
                        self.messages.append(
                            {"role": "assistant", "content": reply}
                        )
                        self._trim_short_term_memory()
                        remember_turn(normalized, reply)
                        return reply

                    if attempt < retries:
                        next_budget = self._next_retry_budget(max_tokens)
                        print(
                            "⚠️ Model returned no visible reply; retrying with "
                            f"a {next_budget}-token output budget."
                        )
                        max_tokens = next_budget
            except Exception:
                # Do not leave an unanswered user message in short-term memory.
                if self.messages and self.messages[-1] == {
                    "role": "user",
                    "content": normalized,
                }:
                    self.messages.pop()
                raise

            if self.messages and self.messages[-1] == {
                "role": "user",
                "content": normalized,
            }:
                self.messages.pop()

            detail = ""
            if last_finish_reason == "length" and last_had_reasoning:
                detail = (
                    " The model used its output budget for reasoning before "
                    "producing a final answer."
                )
            elif last_finish_reason:
                detail = f" Finish reason: {last_finish_reason}."

            raise EmptyModelResponseError(
                "The model returned no visible response after retrying."
                f"{detail} Increase llm.max_tokens, disable reasoning through a "
                "supported backend, or choose a non-reasoning model."
            )


_DEFAULT_LLM: LocalLLM | None = None
_DEFAULT_FINGERPRINT: tuple[Any, ...] | None = None
_DEFAULT_LOCK = threading.RLock()


def _settings_fingerprint(settings: AppSettings) -> tuple[Any, ...]:
    llm_values = asdict(settings.llm)
    return (
        tuple(sorted((key, repr(value)) for key, value in llm_values.items())),
        settings.personality.preset,
        settings.personality.prompt,
    )


def get_default_llm(*, reload: bool = False) -> LocalLLM:
    """Return the configured process-wide LLM client.

    If a future WebUI saves different LLM or personality settings, the next call
    rebuilds the client automatically. Rebuilding intentionally starts a fresh
    short-term conversation while long-term memory remains on disk.
    """

    global _DEFAULT_LLM, _DEFAULT_FINGERPRINT

    settings = get_settings(reload=reload)
    fingerprint = _settings_fingerprint(settings)
    with _DEFAULT_LOCK:
        if _DEFAULT_LLM is None or _DEFAULT_FINGERPRINT != fingerprint:
            _DEFAULT_LLM = LocalLLM(settings)
            _DEFAULT_FINGERPRINT = fingerprint
        return _DEFAULT_LLM


def ask_ai(prompt: str) -> str:
    """Backward-compatible responder used by ``ConversationService``."""

    return get_default_llm().ask(prompt)
