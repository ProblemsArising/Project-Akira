"""Microphone level testing and VAD calibration for Project Akira."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import math
import threading
from typing import Any, Callable

import numpy as np
import scipy.io.wavfile as wav

from audio.devices import resolve_audio_device
from config.settings import AppSettings, get_settings

LevelCallback = Callable[[dict[str, Any]], None]


def _rms(frame: np.ndarray) -> float:
    frame = np.asarray(frame, dtype=np.float32)
    return float(np.sqrt(np.mean(np.square(frame))))


def _float_to_int16(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767).astype(np.int16)


def _supported_input_sample_rate(
    input_device: int | str | None,
    *,
    requested_rate: int,
    channels: int,
    sd_module: Any,
) -> int:
    requested_rate = max(1, int(requested_rate))
    try:
        sd_module.check_input_settings(
            device=input_device,
            channels=channels,
            samplerate=requested_rate,
            dtype="float32",
        )
        return requested_rate
    except Exception as requested_error:
        try:
            info = sd_module.query_devices(input_device, kind="input")
            fallback_rate = int(round(float(info.get("default_samplerate", 0))))
        except Exception as query_error:
            raise CalibrationError(
                f"Could not inspect microphone {input_device!r}: {query_error}"
            ) from requested_error

        if fallback_rate <= 0 or fallback_rate == requested_rate:
            raise CalibrationError(
                f"Microphone {input_device!r} does not support {requested_rate} Hz."
            ) from requested_error

        try:
            sd_module.check_input_settings(
                device=input_device,
                channels=channels,
                samplerate=fallback_rate,
                dtype="float32",
            )
        except Exception as fallback_error:
            raise CalibrationError(
                f"Microphone {input_device!r} supports neither {requested_rate} Hz "
                f"nor its advertised default of {fallback_rate} Hz."
            ) from fallback_error
        return fallback_rate


class CalibrationError(RuntimeError):
    """Raised when a microphone calibration cannot be completed."""


class CalibrationCancelled(CalibrationError):
    """Raised when the current calibration is cancelled."""


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    noise_floor: float
    average_level: float
    peak_level: float
    current_start_threshold: float
    current_end_threshold: float
    suggested_min_start_threshold: float
    suggested_min_end_threshold: float
    sample_rate: int
    duration_seconds: float
    calibration_seconds: float
    sample_file: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AudioCalibrationSession:
    """Capture a short microphone sample and report live RMS levels.

    The first part of the recording estimates the room noise floor. The rest is
    intended for normal speech and is saved as a WAV so the user can hear
    exactly what Project Akira captured.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        input_device: int | str | None | object = ...,
        output_file: str | Path = "data/calibration_sample.wav",
        sd_module: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.audio_settings = self.settings.audio
        self.input_device = (
            self.audio_settings.input_device
            if input_device is ...
            else input_device
        )
        self.output_file = Path(output_file)
        if sd_module is None:
            try:
                import sounddevice as sounddevice_module
            except ImportError as error:
                raise CalibrationError(
                    "The sounddevice package is required for audio calibration."
                ) from error
            sd_module = sounddevice_module
        self.sd_module = sd_module
        self._stop_event = threading.Event()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self) -> None:
        self._stop_event.set()

    @staticmethod
    def _dbfs(level: float) -> float:
        return 20.0 * math.log10(max(float(level), 1e-8))

    def _resolved_device(self) -> int | str | None:
        selector = self.input_device
        if selector is None:
            return None

        try:
            device = resolve_audio_device(selector, "input")
        except Exception as error:
            raise CalibrationError(f"Could not use the selected microphone: {error}") from error
        return None if device is None else device.index

    def run(
        self,
        *,
        duration_seconds: float = 7.0,
        calibration_seconds: float | None = None,
        on_level: LevelCallback | None = None,
    ) -> CalibrationResult:
        """Run one calibration pass and return measured/recommended values."""

        duration_seconds = min(max(float(duration_seconds), 2.0), 20.0)
        calibration_seconds = (
            float(self.audio_settings.calibration_seconds)
            if calibration_seconds is None
            else float(calibration_seconds)
        )
        calibration_seconds = min(
            max(calibration_seconds, 0.3),
            max(0.3, duration_seconds - 0.75),
        )

        if self.stop_requested:
            raise CalibrationCancelled("Calibration was cancelled.")

        device = self._resolved_device()
        channels = max(1, int(self.audio_settings.channels))
        requested_rate = max(1, int(self.audio_settings.sample_rate))
        sample_rate = _supported_input_sample_rate(
            device,
            requested_rate=requested_rate,
            channels=channels,
            sd_module=self.sd_module,
        )
        frame_ms = max(10, int(self.audio_settings.frame_ms))
        frame_size = max(1, int(sample_rate * frame_ms / 1000))
        total_frames = max(1, int(duration_seconds * sample_rate / frame_size))
        quiet_frames = max(1, int(calibration_seconds * sample_rate / frame_size))

        frames: list[np.ndarray] = []
        levels: list[float] = []
        peaks: list[float] = []
        noise_levels: list[float] = []

        try:
            with self.sd_module.InputStream(
                device=device,
                samplerate=sample_rate,
                channels=channels,
                dtype="float32",
                blocksize=frame_size,
            ) as stream:
                for index in range(total_frames):
                    if self.stop_requested:
                        raise CalibrationCancelled("Calibration was cancelled.")

                    frame, overflowed = stream.read(frame_size)
                    frame = np.asarray(frame, dtype=np.float32).copy()
                    level = _rms(frame)
                    peak = float(np.max(np.abs(frame))) if frame.size else 0.0
                    phase = "noise" if index < quiet_frames else "speech"

                    frames.append(frame)
                    levels.append(level)
                    peaks.append(peak)
                    if phase == "noise" and not overflowed:
                        noise_levels.append(level)

                    if on_level is not None:
                        on_level(
                            {
                                "phase": phase,
                                "rms": level,
                                "peak": peak,
                                "dbfs": self._dbfs(level),
                                "progress": min(1.0, (index + 1) / total_frames),
                                "overflowed": bool(overflowed),
                            }
                        )
        except CalibrationCancelled:
            raise
        except Exception as error:
            raise CalibrationError(f"Could not record microphone calibration: {error}") from error

        if not frames:
            raise CalibrationError("No microphone audio was captured.")

        noise_floor = float(np.median(noise_levels)) if noise_levels else 0.0
        average_level = float(np.mean(levels)) if levels else 0.0
        peak_level = max(peaks, default=0.0)

        start_multiplier = float(self.audio_settings.start_threshold_multiplier)
        end_multiplier = float(self.audio_settings.end_threshold_multiplier)
        current_start = max(
            noise_floor * start_multiplier,
            float(self.audio_settings.min_start_threshold),
        )
        current_end = max(
            noise_floor * end_multiplier,
            float(self.audio_settings.min_end_threshold),
        )

        # These are floor values, not replacements for dynamic calibration. A
        # small margin keeps a quiet microphone usable while preventing the
        # threshold from sitting directly on the measured noise floor.
        suggested_start = max(noise_floor * start_multiplier, 0.003)
        suggested_end = max(noise_floor * end_multiplier, 0.0015)
        suggested_end = min(suggested_end, suggested_start * 0.8)

        speech_frames = frames[quiet_frames:] or frames
        audio = np.concatenate(speech_frames, axis=0)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        wav.write(str(self.output_file), sample_rate, _float_to_int16(audio))

        return CalibrationResult(
            noise_floor=noise_floor,
            average_level=average_level,
            peak_level=peak_level,
            current_start_threshold=current_start,
            current_end_threshold=current_end,
            suggested_min_start_threshold=suggested_start,
            suggested_min_end_threshold=suggested_end,
            sample_rate=sample_rate,
            duration_seconds=duration_seconds,
            calibration_seconds=calibration_seconds,
            sample_file=str(self.output_file),
        )
