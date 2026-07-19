"""Configurable local LLM client used by Project Akira."""

from __future__ import annotations

import threading
from dataclasses import asdict
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit


from config.settings import AppSettings, get_settings
from .memory import build_memory_context, remember_turn
from .personality import get_personality


class EmptyModelResponseError(RuntimeError):
    """Raised when the model never produces visible assistant text."""


class LocalLLM:
    """LM Studio/native or OpenAI-compatible chat backend with memory handling."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        client: Any | None = None,
        native_client: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_settings = self.settings.llm

        # Supplying an OpenAI-style client explicitly keeps unit tests and custom
        # integrations on the compatibility endpoint.
        self._force_openai_compatible = client is not None
        self.client = client
        self.native_client = native_client

        if self._uses_native_lm_studio():
            if self.native_client is None:
                import httpx

                self.native_client = httpx.Client(timeout=180.0)
        elif self.client is None:
            from openai import OpenAI

            self.client = OpenAI(
                base_url=self.llm_settings.base_url,
                api_key=self.llm_settings.api_key or "None",
            )
        self.messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": get_personality(self.settings),
            }
        ]
        self._lock = threading.RLock()

    def _uses_native_lm_studio(self) -> bool:
        """Use LM Studio native chat when a reasoning mode must be enforced."""

        backend = str(self.llm_settings.backend or "").strip().casefold()
        reasoning = str(self.llm_settings.reasoning_mode or "auto").strip().casefold()
        return (
            not self._force_openai_compatible
            and backend == "lm_studio"
            and reasoning != "auto"
        )

    def _native_chat_url(self) -> str:
        """Derive LM Studio's native chat URL from the configured server URL."""

        parsed = urlsplit(str(self.llm_settings.base_url).strip())
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                "llm.base_url must include a scheme and host, such as "
                "http://localhost:1234/v1"
            )
        return urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/v1/chat", "", "")
        )

    @staticmethod
    def _native_transcript(
        api_messages: list[dict[str, str]],
    ) -> tuple[str, str]:
        """Flatten local role history for LM Studio's native chat endpoint.

        The native endpoint accepts a system prompt plus user input, but does
        not accept assistant-role messages directly. A labelled transcript
        preserves Project Akira's existing local short-term context.
        """

        system_parts: list[str] = []
        transcript: list[str] = []

        for message in api_messages:
            role = str(message.get("role", "")).strip().casefold()
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                transcript.append(f"Akira: {content}")
            else:
                transcript.append(f"User: {content}")

        system_prompt = "\n\n".join(system_parts)
        if len(transcript) == 1 and transcript[0].startswith("User: "):
            input_text = transcript[0][6:]
        else:
            input_text = (
                "Continue the conversation transcript below. Respond only with "
                "Akira's next reply to the final User message.\n\n"
                + "\n".join(transcript)
                + "\nAkira:"
            )
        return system_prompt, input_text

    @staticmethod
    def _native_output_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, Mapping):
                    value = item.get("text") or item.get("content")
                    if value:
                        parts.append(str(value))
                elif item:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content or "").strip()

    def _create_native_completion(
        self,
        api_messages: list[dict[str, str]],
        *,
        max_tokens: int,
    ) -> dict[str, Any]:
        system_prompt, input_text = self._native_transcript(api_messages)
        reasoning = str(
            self.llm_settings.reasoning_mode or "off"
        ).strip().casefold()

        payload: dict[str, Any] = {
            "model": self.llm_settings.model,
            "input": input_text,
            "system_prompt": system_prompt,
            "temperature": float(self.llm_settings.temperature),
            "top_p": float(self.llm_settings.top_p),
            "max_output_tokens": max(1, int(max_tokens)),
            "reasoning": reasoning,
            "stream": False,
            "store": False,
        }

        headers = {"Content-Type": "application/json"}
        api_key = str(self.llm_settings.api_key or "").strip()
        if api_key and api_key.casefold() != "none":
            headers["Authorization"] = f"Bearer {api_key}"

        response = self.native_client.post(
            self._native_chat_url(),
            json=payload,
            headers=headers,
        )
        try:
            response.raise_for_status()
        except Exception as error:
            detail = str(getattr(response, "text", "") or "").strip()
            suffix = f" Server response: {detail}" if detail else ""
            raise RuntimeError(
                "LM Studio native chat request failed. Ensure LM Studio 0.4.0+ "
                "is running and the selected model supports the requested "
                f"reasoning mode '{reasoning}'.{suffix}"
            ) from error

        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("LM Studio returned an invalid native chat response.")
        result["_akira_native"] = True
        result["_akira_max_tokens"] = max(1, int(max_tokens))
        return result

    def reset_short_term_memory(self) -> None:
        """Clear current-run messages while preserving the system prompt."""

        with self._lock:
            self.messages = [
                {
                    "role": "system",
                    "content": get_personality(self.settings),
                }
            ]

    def set_system_prompt(self, prompt: str) -> None:
        """Replace the personality prompt without discarding chat context."""

        normalized = str(prompt or "").strip()
        if not normalized:
            raise ValueError("Personality prompt cannot be empty.")
        with self._lock:
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0] = {"role": "system", "content": normalized}
            else:
                self.messages.insert(0, {"role": "system", "content": normalized})

    def load_short_term_history(
        self,
        turns: Iterable[tuple[str, str]],
    ) -> None:
        """Replace short-term context with turns from a saved conversation."""

        with self._lock:
            self.messages = [
                {
                    "role": "system",
                    "content": get_personality(self.settings),
                }
            ]

            for user_text, assistant_text in turns:
                normalized_user = str(user_text or "").strip()
                normalized_assistant = str(assistant_text or "").strip()
                if normalized_user:
                    self.messages.append(
                        {"role": "user", "content": normalized_user}
                    )
                if normalized_assistant:
                    self.messages.append(
                        {"role": "assistant", "content": normalized_assistant}
                    )

            self._trim_short_term_memory()

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
        if self._uses_native_lm_studio():
            return self._create_native_completion(
                api_messages,
                max_tokens=max_tokens,
            )

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
        if isinstance(response, Mapping) and response.get("_akira_native"):
            messages: list[str] = []
            reasoning_parts: list[str] = []
            for item in response.get("output", []) or []:
                if not isinstance(item, Mapping):
                    continue
                item_type = str(item.get("type", "")).casefold()
                content = LocalLLM._native_output_text(item.get("content"))
                if item_type == "message" and content:
                    messages.append(content)
                elif item_type == "reasoning" and content:
                    reasoning_parts.append(content)

            stats = response.get("stats") or {}
            total_output = int(stats.get("total_output_tokens") or 0)
            maximum = int(response.get("_akira_max_tokens") or 0)
            finish_reason = (
                "length"
                if maximum > 0 and total_output >= maximum
                else "stop"
            )
            return (
                "\n".join(messages).strip(),
                finish_reason,
                "\n".join(reasoning_parts).strip() or None,
            )

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


def reset_short_term_context() -> None:
    """Start a clean LLM short-term conversation."""

    get_default_llm().reset_short_term_memory()


def load_short_term_context(turns: Iterable[tuple[str, str]]) -> None:
    """Restore a saved conversation into the LLM short-term context."""

    get_default_llm().load_short_term_history(turns)


def refresh_personality_prompt(prompt: str | None = None) -> bool:
    """Apply the active personality to an already-loaded LLM in place.

    Returns ``False`` when the LLM has not been created yet. In that case the
    next call to :func:`get_default_llm` loads the selected preset normally.
    """

    global _DEFAULT_FINGERPRINT

    settings = get_settings(reload=True)
    resolved = str(prompt or get_personality(settings)).strip()
    with _DEFAULT_LOCK:
        if _DEFAULT_LLM is None:
            _DEFAULT_FINGERPRINT = None
            return False
        _DEFAULT_LLM.settings = settings
        _DEFAULT_LLM.llm_settings = settings.llm
        _DEFAULT_LLM.set_system_prompt(resolved)
        _DEFAULT_FINGERPRINT = _settings_fingerprint(settings)
        return True
