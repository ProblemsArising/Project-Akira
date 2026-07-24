"""In-process RVC voice conversion for synthesized Akira audio."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import numpy as np

from audio.tts import SynthesizedAudio


class RVCError(RuntimeError):
    """Base error for Akira's internal RVC pipeline."""


class RVCDependencyError(RVCError):
    """Raised when the optional RVC runtime is not installed."""


class RVCConfigurationError(RVCError):
    """Raised when a voice model or conversion option is invalid."""


class RVCInferenceError(RVCError):
    """Raised when the RVC backend cannot convert an audio buffer."""


@dataclass(frozen=True, slots=True)
class RVCModelConfig:
    """Configuration for one preloaded RVC voice model."""

    model_path: str | Path
    index_path: str | Path | None = None
    only_cpu: bool = False
    pitch_algorithm: str = "rmvpe+"
    pitch_shift: int = 0
    index_influence: float = 0.66
    median_filter_radius: int = 3
    envelope_ratio: float = 0.25
    consonant_protection: float = 0.33
    tag: str | None = None

    def __post_init__(self) -> None:
        model_path = Path(self.model_path).expanduser().resolve()
        if model_path.suffix.lower() != ".pth":
            raise RVCConfigurationError("RVC model_path must point to a .pth file.")
        if not model_path.is_file():
            raise RVCConfigurationError(f"RVC model file does not exist: {model_path}")

        index_path: Path | None = None
        if self.index_path not in (None, ""):
            index_path = Path(self.index_path).expanduser().resolve()
            if index_path.suffix.lower() != ".index":
                raise RVCConfigurationError("RVC index_path must point to a .index file.")
            if not index_path.is_file():
                raise RVCConfigurationError(f"RVC index file does not exist: {index_path}")

        pitch_algorithm = str(self.pitch_algorithm).strip()
        if not pitch_algorithm:
            raise RVCConfigurationError("pitch_algorithm cannot be blank.")

        pitch_shift = int(self.pitch_shift)
        median_filter_radius = int(self.median_filter_radius)
        if median_filter_radius < 0:
            raise RVCConfigurationError("median_filter_radius cannot be negative.")

        index_influence = _validate_ratio("index_influence", self.index_influence)
        envelope_ratio = _validate_ratio("envelope_ratio", self.envelope_ratio)
        consonant_protection = _validate_ratio(
            "consonant_protection",
            self.consonant_protection,
        )

        tag = str(self.tag).strip() if self.tag is not None else model_path.stem
        if not tag:
            raise RVCConfigurationError("tag cannot be blank.")

        object.__setattr__(self, "model_path", model_path)
        object.__setattr__(self, "index_path", index_path)
        object.__setattr__(self, "only_cpu", bool(self.only_cpu))
        object.__setattr__(self, "pitch_algorithm", pitch_algorithm)
        object.__setattr__(self, "pitch_shift", pitch_shift)
        object.__setattr__(self, "index_influence", index_influence)
        object.__setattr__(self, "median_filter_radius", median_filter_radius)
        object.__setattr__(self, "envelope_ratio", envelope_ratio)
        object.__setattr__(self, "consonant_protection", consonant_protection)
        object.__setattr__(self, "tag", tag)


def _validate_ratio(name: str, value: float) -> float:
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise RVCConfigurationError(f"{name} must be between 0.0 and 1.0.")
    return normalized


def _default_loader_factory(**kwargs: Any) -> Any:
    try:
        module = import_module("infer_rvc_python")
        loader_type = getattr(module, "BaseLoader")
    except (ImportError, ModuleNotFoundError, AttributeError) as error:
        raise RVCDependencyError(
            "Internal RVC is not installed. First run "
            "'python -m pip install pip==24.0', then run "
            "'python -m pip install -r requirements-rvc.txt'."
        ) from error

    return loader_type(**kwargs)


def _to_mono_pcm16(audio: SynthesizedAudio) -> np.ndarray:
    samples = audio.samples
    if samples.ndim == 2:
        samples = np.mean(samples, axis=1, dtype=np.float32)

    normalized = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return np.ascontiguousarray(
        np.rint(normalized * np.iinfo(np.int16).max).astype(np.int16)
    )


class RVCConverter:
    """Lazily load one RVC model and convert in-memory TTS audio.

    The third-party runtime is imported only when the first conversion occurs,
    so users who do not enable voice conversion do not pay its startup cost.
    A converter instance keeps its model preloaded for subsequent utterances.
    """

    def __init__(
        self,
        config: RVCModelConfig,
        *,
        hubert_path: str | Path | None = None,
        rmvpe_path: str | Path | None = None,
        loader_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.hubert_path = None if hubert_path is None else str(Path(hubert_path))
        self.rmvpe_path = None if rmvpe_path is None else str(Path(rmvpe_path))
        self._loader_factory = loader_factory or _default_loader_factory
        self._loader: Any | None = None
        self._lock = RLock()

    @property
    def loaded(self) -> bool:
        return self._loader is not None

    def convert(self, audio: SynthesizedAudio) -> SynthesizedAudio:
        """Convert synthesized audio and return a normalized in-memory buffer."""

        if not isinstance(audio, SynthesizedAudio):
            raise TypeError("audio must be a SynthesizedAudio instance")

        input_audio = _to_mono_pcm16(audio)

        with self._lock:
            loader = self._ensure_loaded()
            try:
                result = loader.generate_from_cache(
                    audio_data=(input_audio, audio.sample_rate),
                    tag=self.config.tag,
                )
            except RVCError:
                raise
            except Exception as error:
                raise RVCInferenceError(f"RVC inference failed: {error}") from error

        if not isinstance(result, tuple) or len(result) != 2:
            raise RVCInferenceError(
                "RVC backend returned an invalid result; expected (audio, sample_rate)."
            )

        converted, sample_rate = result
        try:
            return SynthesizedAudio(
                samples=np.asarray(converted),
                sample_rate=int(sample_rate),
            )
        except (TypeError, ValueError) as error:
            raise RVCInferenceError(f"RVC backend returned invalid audio: {error}") from error

    def _ensure_loaded(self) -> Any:
        if self._loader is not None:
            return self._loader

        loader: Any | None = None
        try:
            loader = self._loader_factory(
                only_cpu=self.config.only_cpu,
                hubert_path=self.hubert_path,
                rmvpe_path=self.rmvpe_path,
            )
            loader.apply_conf(
                tag=self.config.tag,
                file_model=str(self.config.model_path),
                pitch_algo=self.config.pitch_algorithm,
                pitch_lvl=self.config.pitch_shift,
                file_index=(
                    "" if self.config.index_path is None else str(self.config.index_path)
                ),
                index_influence=self.config.index_influence,
                respiration_median_filtering=self.config.median_filter_radius,
                envelope_ratio=self.config.envelope_ratio,
                consonant_breath_protection=self.config.consonant_protection,
            )
        except RVCError:
            raise
        except Exception as error:
            if loader is not None:
                try:
                    loader.unload_models()
                except Exception:
                    pass
            raise RVCInferenceError(f"Could not load RVC model: {error}") from error

        self._loader = loader
        return loader

    def close(self) -> None:
        """Unload the active RVC model and release backend resources."""

        with self._lock:
            loader = self._loader
            self._loader = None
            if loader is None:
                return
            try:
                loader.unload_models()
            except Exception as error:
                raise RVCInferenceError(f"Could not unload RVC model: {error}") from error

    def __enter__(self) -> "RVCConverter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
