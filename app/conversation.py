"""Reusable conversation orchestration for Project Akira.

The original project kept the complete voice pipeline in a module-level
``while True`` loop.  ``ConversationService`` owns that orchestration instead,
so a command-line launcher, future WebUI, tests, and game integrations can all
use the same conversation flow.

The service deliberately receives its components as callables.  This keeps the
core lightweight and testable without importing CUDA, audio, TTS, or avatar
libraries.  ``from_default_components`` performs those imports lazily when the
real application starts.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

ConversationSource = Literal["voice", "text"]
Recorder = Callable[[], str | None]
Transcriber = Callable[[str], str]
Responder = Callable[[str], str]
Speaker = Callable[[str], None]
MessageCallback = Callable[[str], None]
ResultCallback = Callable[["ConversationResult"], None]
HistoryErrorCallback = Callable[[Exception], None]


class HistoryStore(Protocol):
    """Minimum history API required by ``ConversationService``."""

    def record_turn(
        self,
        *,
        conversation_id: int | None,
        user_text: str,
        assistant_text: str,
        source: ConversationSource,
        audio_file: str | None = None,
        spoken: bool = False,
        title: str | None = None,
    ) -> tuple[int, int]: ...

    def create_conversation(self, title: str | None = None) -> int: ...


@dataclass(frozen=True, slots=True)
class ConversationResult:
    """The completed result of one user-to-Akira conversation turn."""

    user_text: str
    reply: str
    source: ConversationSource
    audio_file: str | None = None
    spoken: bool = False


class ConversationService:
    """Coordinate recording, transcription, LLM response, and speech output.

    A single turn can originate from either microphone audio or direct text.
    Calls are serialized with a lock so the future WebUI cannot accidentally
    submit a typed message while a voice turn is still generating.
    """

    def __init__(
        self,
        *,
        recorder: Recorder,
        transcriber: Transcriber,
        responder: Responder,
        speaker: Speaker,
        on_user_text: MessageCallback | None = None,
        on_reply: MessageCallback | None = print,
        on_result: ResultCallback | None = None,
        history_store: HistoryStore | None = None,
        conversation_id: int | None = None,
        on_history_error: HistoryErrorCallback | None = None,
    ) -> None:
        self._recorder = recorder
        self._transcriber = transcriber
        self._responder = responder
        self._speaker = speaker
        self._on_user_text = on_user_text
        self._on_reply = on_reply
        self._on_result = on_result
        self._history_store = history_store
        self._conversation_id = conversation_id
        self._on_history_error = on_history_error

        self._turn_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._running = False

    @classmethod
    def from_default_components(cls, **kwargs: object) -> "ConversationService":
        """Create the service using Project Akira's current production modules.

        Imports are intentionally local.  Merely importing this service should
        not initialize Faster-Whisper, CUDA, pyttsx3, or the VMC avatar.
        """

        from ai.llm import ask_ai
        from audio.microphone import record_audio
        from audio.tts import tts
        from audio.whisper_stt import transcribe

        if "history_store" not in kwargs:
            from app.history import get_history_store

            kwargs["history_store"] = get_history_store()

        return cls(
            recorder=record_audio,
            transcriber=transcribe,
            responder=ask_ai,
            speaker=tts,
            **kwargs,
        )

    @property
    def current_conversation_id(self) -> int | None:
        """Database ID used by the current service session."""

        with self._state_lock:
            return self._conversation_id

    def start_new_conversation(self, title: str | None = None) -> int | None:
        """Start a new history conversation for subsequent turns.

        Returns the new database ID, or ``None`` when history is disabled.
        """

        with self._turn_lock:
            if self._history_store is None:
                with self._state_lock:
                    self._conversation_id = None
                return None

            conversation_id = self._history_store.create_conversation(title)
            with self._state_lock:
                self._conversation_id = conversation_id
            return conversation_id

    @property
    def is_running(self) -> bool:
        """Whether ``run_voice_loop`` is currently active."""

        with self._state_lock:
            return self._running

    @property
    def stop_requested(self) -> bool:
        """Whether a graceful stop has been requested."""

        return self._stop_event.is_set()

    def process_text(
        self,
        text: str,
        *,
        speak: bool = True,
        source: ConversationSource = "text",
        audio_file: str | None = None,
    ) -> ConversationResult | None:
        """Process one typed or transcribed message.

        Blank input and blank model responses are ignored and return ``None``.
        The complete turn is serialized so audio and text requests cannot race
        the shared short-term LLM history.
        """

        normalized_text = str(text).strip()
        if not normalized_text:
            return None

        with self._turn_lock:
            if self._on_user_text is not None:
                self._on_user_text(normalized_text)

            reply = str(self._responder(normalized_text)).strip()
            if not reply:
                return None

            if self._on_reply is not None:
                self._on_reply(reply)

            spoken = False
            if speak:
                self._speaker(reply)
                spoken = True

            result = ConversationResult(
                user_text=normalized_text,
                reply=reply,
                source=source,
                audio_file=audio_file,
                spoken=spoken,
            )

            self._save_to_history(result)

            if self._on_result is not None:
                self._on_result(result)

            return result

    def _save_to_history(self, result: ConversationResult) -> None:
        if self._history_store is None:
            return

        with self._state_lock:
            current_id = self._conversation_id

        try:
            conversation_id, _ = self._history_store.record_turn(
                conversation_id=current_id,
                user_text=result.user_text,
                assistant_text=result.reply,
                source=result.source,
                audio_file=result.audio_file,
                spoken=result.spoken,
                title=result.user_text if current_id is None else None,
            )
        except Exception as error:
            # A locked or damaged history database should not prevent Akira from
            # finishing a conversation turn. The WebUI can replace this callback
            # with a visible notification later.
            if self._on_history_error is not None:
                self._on_history_error(error)
            else:
                print(f"⚠️ Could not save chat history: {error}")
            return

        with self._state_lock:
            self._conversation_id = conversation_id

    def process_voice_once(self) -> ConversationResult | None:
        """Record and process one microphone turn.

        Returns ``None`` when recording or transcription produces no usable
        input.  This mirrors the old loop's ``continue`` behavior.
        """

        audio_file = self._recorder()
        if not audio_file:
            return None

        text = self._transcriber(audio_file)
        if not text or not text.strip():
            return None

        return self.process_text(
            text,
            speak=True,
            source="voice",
            audio_file=str(Path(audio_file)),
        )

    def run_voice_loop(self) -> None:
        """Continuously process microphone turns until ``request_stop``.

        ``record_audio`` is currently blocking, so a stop request takes effect
        after the active recording call returns.  Issue #5 can add an
        interruptible recorder when start/stop listening controls are built.
        """

        with self._state_lock:
            if self._running:
                raise RuntimeError("ConversationService is already running")
            self._running = True
            self._stop_event.clear()

        try:
            while not self._stop_event.is_set():
                self.process_voice_once()
        finally:
            with self._state_lock:
                self._running = False

    def request_stop(self) -> None:
        """Ask the active voice loop to stop at its next safe opportunity."""

        self._stop_event.set()
