"""Waveform-derived mouth animation for synthesized and converted speech."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

DEFAULT_LIPSYNC_FPS = 30


@dataclass(frozen=True, slots=True)
class AudioLipSyncProfile:
    """Compact mouth-open envelope synchronized to a final audio buffer."""

    values: tuple[float, ...]
    fps: int
    duration_seconds: float

    def __post_init__(self) -> None:
        fps = int(self.fps)
        duration = max(0.0, float(self.duration_seconds))
        values = tuple(max(0.0, min(1.0, float(value))) for value in self.values)
        if fps <= 0:
            raise ValueError("fps must be greater than zero")
        if not values:
            raise ValueError("values cannot be empty")
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "fps", fps)
        object.__setattr__(self, "duration_seconds", duration)

    def to_event_data(self) -> dict[str, object]:
        """Return a bounded JSON-compatible WebSocket payload."""

        return {
            "fps": self.fps,
            "duration_seconds": round(self.duration_seconds, 4),
            "values": [round(value, 4) for value in self.values],
            "source": "final_audio",
        }


def _mono_float32(samples: np.ndarray) -> np.ndarray:
    data = np.asarray(samples)
    if data.ndim == 2:
        data = np.mean(data.astype(np.float32), axis=1, dtype=np.float32)
    elif data.ndim != 1:
        raise ValueError("samples must be mono or multichannel audio")

    if np.issubdtype(data.dtype, np.floating):
        return np.clip(data.astype(np.float32), -1.0, 1.0)

    info = np.iinfo(data.dtype)
    scale = float(max(abs(info.min), info.max))
    return data.astype(np.float32) / scale


def _smooth(values: Iterable[float], *, attack: float = 0.72, release: float = 0.38) -> list[float]:
    smoothed: list[float] = []
    current = 0.0
    for raw in values:
        target = max(0.0, min(1.0, float(raw)))
        speed = attack if target >= current else release
        current += (target - current) * speed
        smoothed.append(current)
    return smoothed


def build_audio_lipsync_profile(
    samples: np.ndarray,
    sample_rate: int,
    *,
    fps: int = DEFAULT_LIPSYNC_FPS,
) -> AudioLipSyncProfile:
    """Extract a normalized RMS mouth-open envelope from final playback audio."""

    rate = int(sample_rate)
    frame_rate = int(fps)
    if rate <= 0:
        raise ValueError("sample_rate must be greater than zero")
    if frame_rate <= 0:
        raise ValueError("fps must be greater than zero")

    mono = _mono_float32(samples)
    if mono.size == 0:
        raise ValueError("samples cannot be empty")

    frame_size = max(1, int(round(rate / frame_rate)))
    rms_values: list[float] = []
    for start in range(0, mono.size, frame_size):
        frame = mono[start : start + frame_size]
        rms_values.append(float(np.sqrt(np.mean(np.square(frame), dtype=np.float64))))

    rms = np.asarray(rms_values, dtype=np.float32)
    noise_floor = float(np.percentile(rms, 15))
    speech_peak = float(np.percentile(rms, 95))
    span = max(1e-5, speech_peak - noise_floor)
    normalized = np.clip((rms - noise_floor) / span, 0.0, 1.0)
    # A mild gamma curve keeps quiet consonants visible without pinning the jaw open.
    normalized = np.power(normalized, 0.65)
    values = _smooth(normalized.tolist())

    # Force a short clean close at the end even if the WAV ends mid-frame.
    current = values[-1] if values else 0.0
    for _ in range(2):
        current += (0.0 - current) * 0.65
        values.append(current)
    return AudioLipSyncProfile(
        values=tuple(values),
        fps=frame_rate,
        duration_seconds=mono.size / rate,
    )
