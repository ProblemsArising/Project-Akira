"""Text-to-speech synthesis and optional explicit speaker device routing."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import gcd
from pathlib import Path
import tempfile
import time
from typing import Callable, Mapping, Protocol

import numpy as np
import pyttsx3
import scipy.io.wavfile as wav
import sounddevice as sd

from audio.lipsync import build_audio_lipsync_profile
from avatar.vmc import start_talking, stop_talking
from avatar.vmc import start_talking_audio

MOUTH_END_DELAY_SECONDS = 0.05


class TTSPlaybackError(RuntimeError):
    """Raised when synthesized speech cannot be routed to the selected device."""


TTSAnimationEventCallback = Callable[[str, Mapping[str, object]], None]


class AudioConverter(Protocol):
    """Structural interface for an in-memory voice-conversion stage."""

    def convert(self, audio: "SynthesizedAudio") -> "SynthesizedAudio":
        """Return the final audio buffer that should be played."""


def _configure_engine(engine, *, voice_index: int, rate: int, volume: float) -> None:
    voices = engine.getProperty("voices")
    if voices:
        safe_index = min(max(0, int(voice_index)), len(voices) - 1)
        engine.setProperty("voice", voices[safe_index].id)
    engine.setProperty("rate", int(rate))
    engine.setProperty("volume", max(0.0, min(1.0, float(volume))))


def _to_float32(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio)
    if np.issubdtype(audio.dtype, np.floating):
        return np.clip(audio.astype(np.float32), -1.0, 1.0)

    info = np.iinfo(audio.dtype)
    scale = float(max(abs(info.min), info.max))
    return audio.astype(np.float32) / scale


def _to_pcm16(audio: np.ndarray) -> np.ndarray:
    """Convert normalized audio to broadly compatible signed 16-bit PCM."""

    normalized = _to_float32(audio)
    return np.rint(normalized * np.iinfo(np.int16).max).astype(np.int16)


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    """In-memory TTS audio ready for voice conversion or direct playback.

    Samples are always contiguous, normalized ``float32`` data in the range
    ``[-1.0, 1.0]``. Mono audio uses shape ``(frames,)`` and multichannel audio
    uses ``(frames, channels)``.
    """

    samples: np.ndarray
    sample_rate: int

    def __post_init__(self) -> None:
        sample_rate = int(self.sample_rate)
        if sample_rate <= 0:
            raise ValueError("sample_rate must be greater than zero")

        samples = np.ascontiguousarray(_to_float32(self.samples))
        if samples.ndim not in (1, 2):
            raise ValueError("samples must be mono or multichannel audio")
        if samples.shape[0] == 0:
            raise ValueError("samples cannot be empty")
        if samples.ndim == 2 and samples.shape[1] == 0:
            raise ValueError("multichannel audio must contain at least one channel")

        object.__setattr__(self, "samples", samples)
        object.__setattr__(self, "sample_rate", sample_rate)

    @property
    def frames(self) -> int:
        return int(self.samples.shape[0])

    @property
    def channels(self) -> int:
        return 1 if self.samples.ndim == 1 else int(self.samples.shape[1])

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate

    def to_wav_bytes(self) -> bytes:
        """Encode this buffer as a signed 16-bit PCM WAV byte string."""

        buffer = BytesIO()
        wav.write(buffer, self.sample_rate, _to_pcm16(self.samples))
        return buffer.getvalue()

    def write_wav(self, destination: str | Path) -> Path:
        """Write this buffer as a signed 16-bit PCM WAV file."""

        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        wav.write(str(path), self.sample_rate, _to_pcm16(self.samples))
        return path


def _prepare_output_audio(
    audio: np.ndarray,
    sample_rate: int,
    output_device: int | str | None,
) -> tuple[np.ndarray, int]:
    """Adapt channel count/sample rate to the selected output device."""

    data = _to_float32(audio)
    try:
        device_info = sd.query_devices(output_device, kind="output")
    except Exception as error:
        raise TTSPlaybackError(f"Could not open output device {output_device!r}: {error}") from error

    max_channels = int(device_info.get("max_output_channels", 0))
    if max_channels < 1:
        raise TTSPlaybackError(f"Audio device {output_device!r} has no output channels.")

    if data.ndim == 2 and data.shape[1] > max_channels:
        # TTS is normally mono. If a driver produced stereo and the selected
        # endpoint is mono, downmix rather than failing.
        data = np.mean(data, axis=1, dtype=np.float32)

    channels = 1 if data.ndim == 1 else data.shape[1]
    target_rate = int(round(float(device_info.get("default_samplerate", sample_rate))))
    if target_rate <= 0:
        target_rate = int(sample_rate)

    try:
        sd.check_output_settings(
            device=output_device,
            channels=channels,
            samplerate=sample_rate,
            dtype="float32",
        )
        return data, int(sample_rate)
    except Exception:
        # Shared-mode Windows devices commonly prefer 44.1 or 48 kHz while
        # SAPI may render at another rate. Resample to the device default.
        # Import lazily: normal/default TTS playback does not need scipy.signal,
        # and avoiding a module-level import keeps startup/tests lightweight.
        from scipy.signal import resample_poly

        divisor = gcd(int(sample_rate), target_rate)
        data = resample_poly(data, target_rate // divisor, int(sample_rate) // divisor, axis=0)
        data = np.asarray(data, dtype=np.float32)

    try:
        sd.check_output_settings(
            device=output_device,
            channels=1 if data.ndim == 1 else data.shape[1],
            samplerate=target_rate,
            dtype="float32",
        )
    except Exception as error:
        raise TTSPlaybackError(
            f"Selected output device {output_device!r} cannot play synthesized speech: {error}"
        ) from error

    return data, target_rate


class DirectAudioPlayer:
    """Play a normalized Akira audio buffer through sounddevice.

    ``output_device=None`` uses the operating system's default output device.
    Playback is blocking so callers can keep speech state and avatar animation
    synchronized with the complete utterance.
    """

    def __init__(self, output_device: int | str | None = None) -> None:
        self.output_device = output_device

    def play(self, audio: SynthesizedAudio) -> None:
        if not isinstance(audio, SynthesizedAudio):
            raise TypeError("audio must be a SynthesizedAudio instance")

        playback, playback_rate = _prepare_output_audio(
            audio.samples,
            audio.sample_rate,
            self.output_device,
        )
        try:
            sd.play(
                playback,
                samplerate=playback_rate,
                device=self.output_device,
                blocking=True,
            )
        except Exception as error:
            target = (
                "the system default output device"
                if self.output_device is None
                else f"output device {self.output_device!r}"
            )
            raise TTSPlaybackError(
                f"Could not play synthesized speech through {target}: {error}"
            ) from error


def _synthesize_to_wav(
    text: str,
    *,
    voice_index: int,
    rate: int,
    volume: float,
    destination: str | Path | None = None,
) -> Path:
    if destination is None:
        temporary = tempfile.NamedTemporaryFile(prefix="akira-tts-", suffix=".wav", delete=False)
        path = Path(temporary.name)
        temporary.close()
    else:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)

    engine = pyttsx3.init()
    _configure_engine(engine, voice_index=voice_index, rate=rate, volume=volume)
    try:
        engine.save_to_file(text, str(path))
        engine.runAndWait()
    except Exception:
        path.unlink(missing_ok=True)
        raise

    if not path.exists() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        raise TTSPlaybackError("pyttsx3 did not produce a playable WAV file.")
    return path


class TextToSpeech:
    """Callable TTS backend with reusable synthesis and optional playback."""

    def __init__(
        self,
        *,
        output_device: int | str | None = None,
        voice_index: int = 1,
        rate: int = 175,
        volume: float = 1.0,
        mouth_end_delay_seconds: float = MOUTH_END_DELAY_SECONDS,
        audio_converter: AudioConverter | None = None,
        on_event: TTSAnimationEventCallback | None = None,
    ) -> None:
        self.output_device = output_device
        self.voice_index = voice_index
        self.rate = rate
        self.volume = volume
        self.mouth_end_delay_seconds = max(0.0, float(mouth_end_delay_seconds))
        self.audio_converter = audio_converter
        self._on_event = on_event

    def __call__(self, text: str) -> None:
        self.speak(text)

    def set_event_callback(self, callback: TTSAnimationEventCallback | None) -> None:
        """Attach the owning conversation service after construction."""

        self._on_event = callback

    def _emit_event(self, event_type: str, data: Mapping[str, object] | None = None) -> None:
        callback = self._on_event
        if callback is None:
            return
        try:
            callback(event_type, dict(data or {}))
        except Exception:
            # Avatar/WebSocket animation is optional and must not break speech.
            pass

    def synthesize(self, text: str) -> SynthesizedAudio | None:
        """Render text into an in-memory normalized audio buffer.

        The temporary pyttsx3 WAV is deleted before this method returns. Blank
        text produces ``None`` without initializing the TTS engine.
        """

        normalized = str(text).strip()
        if not normalized:
            return None

        wav_path = _synthesize_to_wav(
            normalized,
            voice_index=self.voice_index,
            rate=self.rate,
            volume=self.volume,
        )
        try:
            sample_rate, samples = wav.read(str(wav_path))
            return SynthesizedAudio(samples=samples, sample_rate=int(sample_rate))
        finally:
            wav_path.unlink(missing_ok=True)

    def synthesize_to_wav(
        self,
        text: str,
        destination: str | Path,
    ) -> Path | None:
        """Render text directly to a caller-owned WAV file.

        Existing files are replaced. Blank text produces ``None`` and leaves
        the destination untouched.
        """

        normalized = str(text).strip()
        if not normalized:
            return None

        return _synthesize_to_wav(
            normalized,
            voice_index=self.voice_index,
            rate=self.rate,
            volume=self.volume,
            destination=destination,
        )

    def render(self, text: str) -> SynthesizedAudio | None:
        """Create the final buffer after optional in-memory voice conversion."""

        audio = self.synthesize(text)
        if audio is None or self.audio_converter is None:
            return audio

        converted = self.audio_converter.convert(audio)
        if not isinstance(converted, SynthesizedAudio):
            raise TTSPlaybackError(
                "Audio converter returned an invalid result; expected SynthesizedAudio."
            )
        return converted

    def play_audio(self, audio: SynthesizedAudio) -> None:
        """Play an already synthesized or converted buffer directly."""

        DirectAudioPlayer(self.output_device).play(audio)

    def speak(self, text: str) -> None:
        normalized = str(text).strip()
        if not normalized:
            return

        # Preserve SAPI's original direct path when no conversion or explicit
        # routing is requested. RVC must always use the buffer path so the
        # converted result, not the source TTS voice, reaches the speakers.
        if self.audio_converter is None and self.output_device is None:
            self._speak_to_system_default(normalized)
        else:
            self._speak_to_audio_buffer(normalized)

    def _speak_to_system_default(self, text: str) -> None:
        """Preserve the original pyttsx3 behavior when no device is selected."""

        engine = pyttsx3.init()
        _configure_engine(
            engine,
            voice_index=self.voice_index,
            rate=self.rate,
            volume=self.volume,
        )
        self._emit_event("avatar.lipsync.text", {"text": text, "source": "tts_text"})
        try:
            start_talking(text)
            engine.say(text)
            engine.runAndWait()
            time.sleep(self.mouth_end_delay_seconds)
        finally:
            stop_talking()
            self._emit_event("avatar.lipsync.stopped", {})

    def _speak_to_audio_buffer(self, text: str) -> None:
        final_audio = self.render(text)
        if final_audio is None:
            return

        profile = build_audio_lipsync_profile(
            final_audio.samples,
            final_audio.sample_rate,
        )
        self._emit_event("avatar.lipsync.started", profile.to_event_data())
        try:
            start_talking_audio(profile.values, profile.fps, text=text)
            self.play_audio(final_audio)
            time.sleep(self.mouth_end_delay_seconds)
        finally:
            stop_talking()
            self._emit_event("avatar.lipsync.stopped", {})


def create_speaker(
    *,
    output_device: int | str | None = None,
    voice_index: int = 1,
    rate: int = 175,
    volume: float = 1.0,
    mouth_end_delay_seconds: float = MOUTH_END_DELAY_SECONDS,
    audio_converter: AudioConverter | None = None,
    on_event: TTSAnimationEventCallback | None = None,
) -> TextToSpeech:
    """Create a configured speaker callable for ``ConversationService``."""

    return TextToSpeech(
        output_device=output_device,
        voice_index=voice_index,
        rate=rate,
        volume=volume,
        mouth_end_delay_seconds=mouth_end_delay_seconds,
        audio_converter=audio_converter,
        on_event=on_event,
    )


# Backward-compatible function used by older code and external scripts.
def tts(text: str) -> None:
    from audio.devices import resolve_audio_device
    from config.settings import get_settings

    settings = get_settings()
    output = resolve_audio_device(settings.audio.output_device, "output")
    create_speaker(
        output_device=None if output is None else output.index,
        voice_index=settings.tts.voice_index,
        rate=settings.tts.rate,
        volume=settings.tts.volume,
        mouth_end_delay_seconds=settings.avatar.mouth_end_delay_seconds,
    ).speak(text)
