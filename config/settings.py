"""Persistent application settings for Project Akira.

The settings system intentionally uses only Python's standard library so it can
be imported very early during application startup. Settings are stored as JSON
and merged with typed defaults whenever they are loaded, which lets future
versions add new options without breaking an existing user's file.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, TypeVar

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_FILE = PROJECT_ROOT / "data" / "settings.json"
SETTINGS_ENV_VAR = "AKIRA_SETTINGS_FILE"


@dataclass
class GeneralSettings:
    launch_on_startup: bool = False
    open_avatar_window: bool = True
    avatar_always_on_top: bool = False
    remember_window_positions: bool = True


@dataclass
class LLMSettings:
    backend: str = "lm_studio"
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "None"
    model: str = "gemma4-12b-qat-uncensored-hauhaucs-balanced"
    temperature: float = 0.75
    top_p: float = 0.9
    max_tokens: int = 180
    max_short_term_messages: int = 20


@dataclass
class STTSettings:
    model: str = "base"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = None


@dataclass
class AudioSettings:
    input_device: int | str | None = None
    output_device: int | str | None = None
    sample_rate: int = 16000
    channels: int = 1
    frame_ms: int = 30
    pre_roll_seconds: float = 0.35
    end_silence_seconds: float = 2.0
    min_record_seconds: float = 0.45
    max_record_seconds: float = 45.0
    calibration_seconds: float = 0.6
    start_threshold_multiplier: float = 6.0
    end_threshold_multiplier: float = 1.8
    min_start_threshold: float = 0.02
    min_end_threshold: float = 0.006


@dataclass
class TTSSettings:
    voice_index: int = 1
    rate: int = 175
    volume: float = 1.0


@dataclass
class AvatarSettings:
    enabled: bool = True
    backend: str = "vmc"
    vmc_ip: str = "127.0.0.1"
    vmc_port: int = 39539
    face_blend_fps: int = 120
    mouth_start_delay_seconds: float = 1.15
    mouth_end_delay_seconds: float = 0.05


@dataclass
class MemorySettings:
    max_turns: int = 300
    max_facts: int = 200
    max_context_chars: int = 2500
    recent_turns: int = 6
    relevant_limit: int = 6


@dataclass
class AppSettings:
    """All user-configurable Project Akira settings."""

    schema_version: int = 1
    general: GeneralSettings = field(default_factory=GeneralSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    stt: STTSettings = field(default_factory=STTSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    avatar: AvatarSettings = field(default_factory=AvatarSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


T = TypeVar("T")
_SETTINGS_LOCK = threading.RLock()
_SETTINGS_CACHE: AppSettings | None = None
_SETTINGS_CACHE_PATH: Path | None = None


def _settings_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()

    environment_path = os.getenv(SETTINGS_ENV_VAR)
    if environment_path:
        return Path(environment_path).expanduser().resolve()

    return DEFAULT_SETTINGS_FILE


def _dataclass_from_mapping(cls: type[T], value: Any) -> T:
    """Build a dataclass while ignoring unknown keys and filling defaults."""
    if not isinstance(value, Mapping):
        return cls()

    valid_names = {field.name for field in fields(cls)}
    filtered = {key: item for key, item in value.items() if key in valid_names}

    try:
        return cls(**filtered)
    except (TypeError, ValueError):
        # A hand-edited setting with an invalid value should not prevent Akira
        # from starting. Individual validation can become stricter in the WebUI.
        return cls()


def _from_dict(data: Any) -> AppSettings:
    if not isinstance(data, Mapping):
        return AppSettings()

    schema_version = data.get("schema_version", 1)
    if not isinstance(schema_version, int):
        schema_version = 1

    return AppSettings(
        schema_version=schema_version,
        general=_dataclass_from_mapping(GeneralSettings, data.get("general")),
        llm=_dataclass_from_mapping(LLMSettings, data.get("llm")),
        stt=_dataclass_from_mapping(STTSettings, data.get("stt")),
        audio=_dataclass_from_mapping(AudioSettings, data.get("audio")),
        tts=_dataclass_from_mapping(TTSSettings, data.get("tts")),
        avatar=_dataclass_from_mapping(AvatarSettings, data.get("avatar")),
        memory=_dataclass_from_mapping(MemorySettings, data.get("memory")),
    )


def save_settings(settings: AppSettings, path: str | Path | None = None) -> Path:
    """Atomically save settings and return the file path used."""
    target = _settings_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")

    with _SETTINGS_LOCK:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(settings.to_dict(), file, indent=2, ensure_ascii=False)
            file.write("\n")
        temporary.replace(target)

    return target


def _backup_invalid_file(path: Path) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.broken-{timestamp}{path.suffix}")
    try:
        path.replace(backup)
        print(f"⚠️ Invalid settings file backed up to: {backup}")
    except OSError:
        pass


def load_settings(
    path: str | Path | None = None,
    *,
    create_if_missing: bool = True,
) -> AppSettings:
    """Load settings, merging missing fields with current defaults."""
    target = _settings_path(path)

    with _SETTINGS_LOCK:
        if not target.exists():
            settings = AppSettings()
            if create_if_missing:
                save_settings(settings, target)
            return settings

        try:
            with target.open("r", encoding="utf-8") as file:
                raw = json.load(file)
        except (json.JSONDecodeError, OSError):
            _backup_invalid_file(target)
            settings = AppSettings()
            if create_if_missing:
                save_settings(settings, target)
            return settings

        settings = _from_dict(raw)

        # Rewrite after loading so newly introduced default fields appear in an
        # older settings file automatically.
        if create_if_missing:
            save_settings(settings, target)

        return settings


def get_settings(*, reload: bool = False) -> AppSettings:
    """Return the process-wide settings object."""
    global _SETTINGS_CACHE, _SETTINGS_CACHE_PATH

    target = _settings_path()
    with _SETTINGS_LOCK:
        if reload or _SETTINGS_CACHE is None or _SETTINGS_CACHE_PATH != target:
            _SETTINGS_CACHE = load_settings(target)
            _SETTINGS_CACHE_PATH = target
        return _SETTINGS_CACHE


def reload_settings() -> AppSettings:
    """Reload settings from disk and replace the process-wide cache."""
    return get_settings(reload=True)


def update_settings(changes: Mapping[str, Any]) -> AppSettings:
    """Merge a nested mapping into the current settings and persist it.

    This API is deliberately shaped for the future WebUI, which will be able to
    send updates such as::

        update_settings({"audio": {"end_silence_seconds": 1.4}})
    """
    global _SETTINGS_CACHE, _SETTINGS_CACHE_PATH

    current = get_settings().to_dict()
    merged = _deep_merge(current, changes)
    updated = _from_dict(merged)
    target = save_settings(updated)

    with _SETTINGS_LOCK:
        _SETTINGS_CACHE = updated
        _SETTINGS_CACHE_PATH = target

    return updated


def _deep_merge(base: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in changes.items():
        existing = result.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def reset_settings() -> AppSettings:
    """Restore defaults, save them, and replace the process-wide cache."""
    global _SETTINGS_CACHE, _SETTINGS_CACHE_PATH

    defaults = AppSettings()
    target = save_settings(defaults)
    with _SETTINGS_LOCK:
        _SETTINGS_CACHE = defaults
        _SETTINGS_CACHE_PATH = target
    return defaults


if __name__ == "__main__":
    settings = get_settings()
    print(f"Settings file: {_settings_path()}")
    print(json.dumps(settings.to_dict(), indent=2, ensure_ascii=False))
