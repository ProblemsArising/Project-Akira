"""Configurable Faster-Whisper speech-to-text backend."""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.paths import BUNDLE_ROOT
from config.settings import AppSettings, get_settings

AUDIO_FILE = "input.wav"

_DLL_HANDLES: list[Any] = []
_DLLS_CONFIGURED = False
_DLL_LOCK = threading.RLock()


def add_cuda_dll_dirs() -> None:
    """Add pip-installed NVIDIA DLL folders on Windows once per process."""

    global _DLLS_CONFIGURED
    with _DLL_LOCK:
        if _DLLS_CONFIGURED:
            return

        site_packages = Path(sys.prefix) / "Lib" / "site-packages"
        dll_dirs = [
            # Source/virtual-environment locations.
            site_packages / "nvidia" / "cublas" / "bin",
            site_packages / "nvidia" / "cudnn" / "bin",
            site_packages / "nvidia" / "cuda_runtime" / "bin",
            site_packages / "torch" / "lib",
            site_packages / "~orch" / "lib",
            # PyInstaller one-folder locations.
            BUNDLE_ROOT,
            BUNDLE_ROOT / "nvidia" / "cublas" / "bin",
            BUNDLE_ROOT / "nvidia" / "cudnn" / "bin",
            BUNDLE_ROOT / "nvidia" / "cuda_runtime" / "bin",
            BUNDLE_ROOT / "ctranslate2",
        ]

        seen: set[Path] = set()
        for folder in dll_dirs:
            folder = folder.resolve()
            if folder in seen or not folder.exists():
                continue
            seen.add(folder)
            if hasattr(os, "add_dll_directory"):
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(str(folder)))
                except OSError:
                    pass
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            print(f"✅ Added DLL path: {folder}")

        _DLLS_CONFIGURED = True


class WhisperTranscriber:
    """Lazy Faster-Whisper model configured from ``AppSettings``."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        model_factory: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.stt_settings = self.settings.stt
        self._model_factory = model_factory
        self._model: Any | None = None
        self._lock = threading.RLock()

    def _load_model(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model

            if str(self.stt_settings.device).lower().startswith("cuda"):
                add_cuda_dll_dirs()

            factory = self._model_factory
            if factory is None:
                from faster_whisper import WhisperModel

                factory = WhisperModel

            self._model = factory(
                self.stt_settings.model,
                device=self.stt_settings.device,
                compute_type=self.stt_settings.compute_type,
            )
            return self._model

    def transcribe(self, audio_file: str = AUDIO_FILE) -> str:
        model = self._load_model()
        options: dict[str, Any] = {
            "beam_size": max(1, int(self.stt_settings.beam_size)),
        }
        if self.stt_settings.language:
            options["language"] = self.stt_settings.language

        segments, _ = model.transcribe(audio_file, **options)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        print("You said:", text)
        return text


_DEFAULT_TRANSCRIBER: WhisperTranscriber | None = None
_DEFAULT_FINGERPRINT: tuple[tuple[str, str], ...] | None = None
_DEFAULT_LOCK = threading.RLock()


def _fingerprint(settings: AppSettings) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, repr(value)) for key, value in asdict(settings.stt).items()))


def get_transcriber(*, reload: bool = False) -> WhisperTranscriber:
    global _DEFAULT_TRANSCRIBER, _DEFAULT_FINGERPRINT

    settings = get_settings(reload=reload)
    fingerprint = _fingerprint(settings)
    with _DEFAULT_LOCK:
        if _DEFAULT_TRANSCRIBER is None or _DEFAULT_FINGERPRINT != fingerprint:
            _DEFAULT_TRANSCRIBER = WhisperTranscriber(settings)
            _DEFAULT_FINGERPRINT = fingerprint
        return _DEFAULT_TRANSCRIBER


def transcribe(audio_file: str = AUDIO_FILE) -> str:
    """Backward-compatible STT function used by ``ConversationService``."""

    return get_transcriber().transcribe(audio_file)
