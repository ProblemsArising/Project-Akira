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
RecorderControl = Callable[[], None]
ListeningStateCallback = Callable[[bool], None]


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
        stop_recorder: RecorderControl | None = None,
        reset_recorder: RecorderControl | None = None,
        on_listening_changed: ListeningStateCallback | None = None,
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
        self._stop_recorder = stop_recorder
        self._reset_recorder = reset_recorder
        self._on_listening_changed = on_listening_changed

        self._turn_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._running = False
        self._listening_active = False
        self._listener_thread: threading.Thread | None = None

    @staticmethod
    def _add_default_history(kwargs: dict[str, object]) -> None:
        """Add the default SQLite history store unless one was supplied."""

        if "history_store" not in kwargs:
            from app.history import get_history_store

            kwargs["history_store"] = get_history_store()

    @classmethod
    def from_default_components(cls, **kwargs: object) -> "ConversationService":
        """Create the service using the complete production voice pipeline.

        Imports are intentionally local. Merely importing this service should
        not initialize Faster-Whisper, CUDA, pyttsx3, or the VMC avatar.
        """

        from ai.llm import ask_ai
        from audio.microphone import MicrophoneRecorder
        from audio.tts import tts
        from audio.whisper_stt import transcribe

        microphone = MicrophoneRecorder()
        cls._add_default_history(kwargs)

        return cls(
            recorder=microphone.record,
            transcriber=transcribe,
            responder=ask_ai,
            speaker=tts,
            stop_recorder=microphone.request_stop,
            reset_recorder=microphone.reset,
            **kwargs,
        )

    @classmethod
    def from_text_components(
        cls,
        *,
        enable_speech: bool = True,
        **kwargs: object,
    ) -> "ConversationService":
        """Create a text-only service without importing microphone or Whisper.

        When ``enable_speech`` is false, pyttsx3 and the avatar controller are
        not imported either. This gives the future WebUI a lightweight typed
        chat path and lets text-only users run Akira without audio input setup.
        """

        from ai.llm import ask_ai

        if enable_speech:
            from audio.tts import tts

            speaker: Speaker = tts
        else:
            speaker = lambda reply: None

        cls._add_default_history(kwargs)

        return cls(
            recorder=lambda: None,
            transcriber=lambda audio_file: "",
            responder=ask_ai,
            speaker=speaker,
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
        """Whether the microphone worker loop is still running."""

        with self._state_lock:
            return self._running

    @property
    def is_listening(self) -> bool:
        """Whether new microphone input is currently being accepted."""

        with self._state_lock:
            return self._listening_active

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

    def _notify_listening_changed(self, listening: bool) -> None:
        callback = self._on_listening_changed
        if callback is not None:
            callback(listening)

    def _claim_listening(self, thread: threading.Thread | None) -> bool:
        with self._state_lock:
            if self._running:
                return False
            self._running = True
            self._listening_active = True
            self._listener_thread = thread
            self._stop_event.clear()

        try:
            if self._reset_recorder is not None:
                self._reset_recorder()
        except Exception:
            with self._state_lock:
                self._running = False
                self._listening_active = False
                self._listener_thread = None
            raise

        self._notify_listening_changed(True)
        return True

    def _finish_listening(self) -> None:
        with self._state_lock:
            should_notify = self._listening_active
            self._running = False
            self._listening_active = False
            self._listener_thread = None
        if should_notify:
            self._notify_listening_changed(False)

    def _run_claimed_voice_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self.process_voice_once()
        finally:
            self._finish_listening()

    def run_voice_loop(self) -> None:
        """Run the microphone loop in the current thread until stopped."""

        if not self._claim_listening(None):
            raise RuntimeError("ConversationService is already listening")
        self._run_claimed_voice_loop()

    def start_listening(self) -> bool:
        """Start microphone listening in a background thread.

        Returns ``True`` when a new loop starts and ``False`` if listening was
        already active. This is the method a future WebUI Start button should
        call.
        """

        thread = threading.Thread(
            target=self._run_claimed_voice_loop,
            name="ProjectAkiraListening",
            daemon=True,
        )

        if not self._claim_listening(thread):
            return False

        try:
            thread.start()
        except Exception:
            self._finish_listening()
            raise
        return True

    def stop_listening(
        self,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> bool:
        """Stop accepting microphone input.

        The interruptible production recorder exits within roughly one audio
        frame. Set ``wait=True`` when a caller needs the background loop fully
        stopped before continuing. Returns whether listening had been active.
        """

        with self._state_lock:
            was_listening = self._listening_active
            self._listening_active = False
            thread = self._listener_thread

        self._stop_event.set()
        if was_listening:
            self._notify_listening_changed(False)
        if self._stop_recorder is not None:
            try:
                self._stop_recorder()
            except Exception as error:
                print(f"⚠️ Could not interrupt microphone recording: {error}")

        if (
            wait
            and thread is not None
            and thread is not threading.current_thread()
            and thread.ident is not None
        ):
            thread.join(timeout=timeout)

        return was_listening

    def wait_until_stopped(self, timeout: float | None = None) -> bool:
        """Wait for a background listening loop to finish."""

        with self._state_lock:
            thread = self._listener_thread

        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.ident is not None
        ):
            thread.join(timeout=timeout)

        return not self.is_running

    def request_stop(self) -> None:
        """Backward-compatible alias for non-blocking ``stop_listening``."""

        self.stop_listening(wait=False)
