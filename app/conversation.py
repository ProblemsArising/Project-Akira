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

import importlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, Protocol

if TYPE_CHECKING:
    from ai.llm_backend import LLMBackend

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
ConversationEventCallback = Callable[[str, Mapping[str, Any]], None]
ConversationContextLoader = Callable[[list[tuple[str, str]]], None]
ConversationContextResetter = Callable[[], None]


def _reply_expression_metadata(text: str) -> dict[str, object]:
    """Classify a reply for avatar clients without risking the chat turn."""

    try:
        from avatar.expression_keywords import (
            best_expression_score,
            expression_from_text,
        )

        return {
            "preset": expression_from_text(text),
            "score": best_expression_score(text),
        }
    except Exception:
        # Avatar classification is optional and must never break a reply.
        return {"preset": "soft", "score": 0}


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

    def get_conversation(self, conversation_id: int) -> Any | None: ...

    def get_turns(self, conversation_id: int, *, limit: int | None = None) -> list[Any]: ...


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
        on_event: ConversationEventCallback | None = None,
        load_conversation_context: ConversationContextLoader | None = None,
        reset_conversation_context: ConversationContextResetter | None = None,
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
        self._load_conversation_context = load_conversation_context
        self._reset_conversation_context = reset_conversation_context
        self._event_callbacks: list[ConversationEventCallback] = []
        if on_event is not None:
            self._event_callbacks.append(on_event)

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
    def from_default_components(
        cls,
        *,
        llm_backend: "LLMBackend | None" = None,
        **kwargs: object,
    ) -> "ConversationService":
        """Create the service using the complete production voice pipeline.

        Imports are intentionally local. Merely importing this service should
        not initialize Faster-Whisper, CUDA, pyttsx3, or the VMC avatar.
        """

        if llm_backend is None:
            llm_module = importlib.import_module("ai.llm")

            responder = llm_module.ask_ai
            load_short_term_context = getattr(
                llm_module,
                "load_short_term_context",
                lambda turns: None,
            )
            reset_short_term_context = getattr(
                llm_module,
                "reset_short_term_context",
                lambda: None,
            )
        else:
            responder = llm_backend.ask
            load_short_term_context = llm_backend.load_short_term_history
            reset_short_term_context = llm_backend.reset_short_term_memory
        from audio.devices import resolve_audio_device
        from audio.microphone import MicrophoneRecorder
        from audio.tts import create_speaker
        from audio.whisper_stt import transcribe
        from app.paths import user_file_path
        from config.settings import get_settings

        settings = get_settings()
        input_selection = resolve_audio_device(settings.audio.input_device, "input")
        output_selection = resolve_audio_device(settings.audio.output_device, "output")

        microphone = MicrophoneRecorder(
            output_file=user_file_path(settings.audio.recording_file),
            input_device=None if input_selection is None else input_selection.index,
            sample_rate=settings.audio.sample_rate,
            channels=settings.audio.channels,
            frame_ms=settings.audio.frame_ms,
            pre_roll_seconds=settings.audio.pre_roll_seconds,
            end_silence_seconds=settings.audio.end_silence_seconds,
            min_record_seconds=settings.audio.min_record_seconds,
            max_record_seconds=settings.audio.max_record_seconds,
            calibration_seconds=settings.audio.calibration_seconds,
            start_threshold_multiplier=settings.audio.start_threshold_multiplier,
            end_threshold_multiplier=settings.audio.end_threshold_multiplier,
            min_start_threshold=settings.audio.min_start_threshold,
            min_end_threshold=settings.audio.min_end_threshold,
        )
        speaker = create_speaker(
            output_device=None if output_selection is None else output_selection.index,
            voice_index=settings.tts.voice_index,
            rate=settings.tts.rate,
            volume=settings.tts.volume,
            mouth_end_delay_seconds=settings.avatar.mouth_end_delay_seconds,
        )
        cls._add_default_history(kwargs)

        return cls(
            recorder=microphone.record,
            transcriber=transcribe,
            responder=responder,
            speaker=speaker,
            stop_recorder=microphone.request_stop,
            reset_recorder=microphone.reset,
            load_conversation_context=load_short_term_context,
            reset_conversation_context=reset_short_term_context,
            **kwargs,
        )

    @classmethod
    def from_text_components(
        cls,
        *,
        enable_speech: bool = True,
        llm_backend: "LLMBackend | None" = None,
        **kwargs: object,
    ) -> "ConversationService":
        """Create a text-only service without importing microphone or Whisper.

        When ``enable_speech`` is false, pyttsx3 and the avatar controller are
        not imported either. This gives the future WebUI a lightweight typed
        chat path and lets text-only users run Akira without audio input setup.
        """

        if llm_backend is None:
            llm_module = importlib.import_module("ai.llm")

            responder = llm_module.ask_ai
            load_short_term_context = getattr(
                llm_module,
                "load_short_term_context",
                lambda turns: None,
            )
            reset_short_term_context = getattr(
                llm_module,
                "reset_short_term_context",
                lambda: None,
            )
        else:
            responder = llm_backend.ask
            load_short_term_context = llm_backend.load_short_term_history
            reset_short_term_context = llm_backend.reset_short_term_memory

        if enable_speech:
            from audio.devices import resolve_audio_device
            from audio.tts import create_speaker
            from config.settings import get_settings

            settings = get_settings()
            output_selection = resolve_audio_device(settings.audio.output_device, "output")
            speaker: Speaker = create_speaker(
                output_device=None if output_selection is None else output_selection.index,
                voice_index=settings.tts.voice_index,
                rate=settings.tts.rate,
                volume=settings.tts.volume,
                mouth_end_delay_seconds=settings.avatar.mouth_end_delay_seconds,
            )
        else:
            speaker = lambda reply: None

        cls._add_default_history(kwargs)

        return cls(
            recorder=lambda: None,
            transcriber=lambda audio_file: "",
            responder=responder,
            speaker=speaker,
            load_conversation_context=load_short_term_context,
            reset_conversation_context=reset_short_term_context,
            **kwargs,
        )

    def add_event_listener(self, callback: ConversationEventCallback) -> None:
        """Subscribe to structured service events.

        Listeners may be called from the main thread, FastAPI worker threads,
        or the background microphone thread. Listener failures are isolated so
        a WebUI connection can never break the conversation pipeline.
        """

        with self._state_lock:
            if callback not in self._event_callbacks:
                self._event_callbacks.append(callback)

    def remove_event_listener(self, callback: ConversationEventCallback) -> None:
        with self._state_lock:
            self._event_callbacks = [
                item for item in self._event_callbacks if item != callback
            ]

    def _emit_event(
        self,
        event_type: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        with self._state_lock:
            callbacks = tuple(self._event_callbacks)

        payload = dict(data or {})
        for callback in callbacks:
            try:
                callback(event_type, payload)
            except Exception:
                # Live UI updates are optional. Never allow one failed client or
                # event handler to interrupt recording, LLM, history, or TTS.
                continue

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
            if self._reset_conversation_context is not None:
                self._reset_conversation_context()

            if self._history_store is None:
                with self._state_lock:
                    self._conversation_id = None
                self._emit_event(
                    "conversation.changed",
                    {"conversation_id": None, "title": title},
                )
                return None

            conversation_id = self._history_store.create_conversation(title)
            with self._state_lock:
                self._conversation_id = conversation_id
            self._emit_event(
                "conversation.changed",
                {"conversation_id": conversation_id, "title": title},
            )
            return conversation_id

    def activate_conversation(self, conversation_id: int) -> int:
        """Resume a saved conversation and restore its recent LLM context."""

        if self._history_store is None:
            raise RuntimeError("Chat history is disabled.")

        selected_id = int(conversation_id)

        with self._turn_lock:
            summary = self._history_store.get_conversation(selected_id)
            if summary is None:
                raise KeyError(f"Conversation {selected_id} does not exist")

            turns = self._history_store.get_turns(selected_id)
            context = [
                (str(turn.user_text), str(turn.assistant_text))
                for turn in turns
            ]

            if self._load_conversation_context is not None:
                self._load_conversation_context(context)

            with self._state_lock:
                self._conversation_id = selected_id

            self._emit_event(
                "conversation.changed",
                {
                    "conversation_id": selected_id,
                    "title": getattr(summary, "title", None),
                    "resumed": True,
                    "restored_turns": len(turns),
                },
            )
            return selected_id

    def detach_conversation(self, conversation_id: int | None = None) -> bool:
        """Forget the active history ID without creating another conversation.

        The history page calls this when the currently active conversation is
        deleted. The next completed turn will then create a fresh conversation
        normally instead of trying to append to a deleted SQLite row.
        """

        with self._turn_lock:
            with self._state_lock:
                current = self._conversation_id
                if current is None:
                    return False
                if conversation_id is not None and current != int(conversation_id):
                    return False
                self._conversation_id = None

            if self._reset_conversation_context is not None:
                self._reset_conversation_context()

            self._emit_event(
                "conversation.changed",
                {"conversation_id": None, "title": None},
            )
            return True

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
            self._emit_event(
                "chat.started",
                {
                    "user_text": normalized_text,
                    "source": source,
                    "speak": bool(speak),
                    "audio_file": audio_file,
                },
            )

            try:
                if self._on_user_text is not None:
                    self._on_user_text(normalized_text)

                reply = str(self._responder(normalized_text)).strip()
                if not reply:
                    self._emit_event(
                        "chat.failed",
                        {
                            "user_text": normalized_text,
                            "source": source,
                            "reason": "empty_reply",
                        },
                    )
                    return None

                if self._on_reply is not None:
                    self._on_reply(reply)

                self._emit_event(
                    "chat.reply_ready",
                    {
                        "user_text": normalized_text,
                        "reply": reply,
                        "source": source,
                        "will_speak": bool(speak),
                        "expression": _reply_expression_metadata(reply),
                    },
                )

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

                self._emit_event(
                    "chat.completed",
                    {
                        "user_text": result.user_text,
                        "reply": result.reply,
                        "source": result.source,
                        "audio_file": result.audio_file,
                        "spoken": result.spoken,
                        "conversation_id": self.current_conversation_id,
                    },
                )
                return result
            except Exception as error:
                self._emit_event(
                    "chat.failed",
                    {
                        "user_text": normalized_text,
                        "source": source,
                        "reason": "exception",
                        "error": str(error),
                    },
                )
                raise

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
            self._emit_event(
                "history.error",
                {"error": str(error), "conversation_id": current_id},
            )
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
        input. This mirrors the old loop's ``continue`` behavior.
        """

        self._emit_event("voice.recording.started", {})
        audio_file = self._recorder()
        if not audio_file:
            self._emit_event("voice.recording.cancelled", {})
            return None

        normalized_audio_file = str(Path(audio_file))
        self._emit_event(
            "voice.recording.completed",
            {"audio_file": normalized_audio_file},
        )
        self._emit_event(
            "voice.transcription.started",
            {"audio_file": normalized_audio_file},
        )

        text = self._transcriber(audio_file)
        if not text or not text.strip():
            self._emit_event(
                "voice.transcription.empty",
                {"audio_file": normalized_audio_file},
            )
            return None

        normalized_text = text.strip()
        self._emit_event(
            "voice.transcription.completed",
            {
                "audio_file": normalized_audio_file,
                "text": normalized_text,
            },
        )

        return self.process_text(
            normalized_text,
            speak=True,
            source="voice",
            audio_file=normalized_audio_file,
        )

    def _notify_listening_changed(self, listening: bool) -> None:
        self._emit_event(
            "listening.changed",
            {
                "is_listening": bool(listening),
                "is_running": self.is_running,
            },
        )
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
