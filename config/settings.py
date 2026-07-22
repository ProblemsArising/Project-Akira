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

from app.paths import PROJECT_ROOT, USER_DATA_ROOT

DEFAULT_SETTINGS_FILE = USER_DATA_ROOT / "settings.json"
SETTINGS_ENV_VAR = "AKIRA_SETTINGS_FILE"
CURRENT_SCHEMA_VERSION = 5


@dataclass
class GeneralSettings:
    launch_on_startup: bool = False
    system_tray_enabled: bool = True
    close_to_tray: bool = True
    open_avatar_window: bool = True
    avatar_always_on_top: bool = False
    avatar_transparent_window: bool = False
    remember_window_positions: bool = True

    # Native desktop-window bounds in logical pixels. ``None`` lets pywebview
    # center a window on its first launch.
    main_window_x: int | None = None
    main_window_y: int | None = None
    main_window_width: int | None = None
    main_window_height: int | None = None
    main_window_maximized: bool = False

    avatar_window_x: int | None = None
    avatar_window_y: int | None = None
    avatar_window_width: int | None = None
    avatar_window_height: int | None = None
    avatar_window_maximized: bool = False


@dataclass
class LLMSettings:
    backend: str = "lm_studio"
    base_url: str = "http://localhost:1234/v1"
    # Remember the most recently used endpoint for each external backend so
    # switching backend cards on the Models page restores the right URL.
    lm_studio_base_url: str = "http://localhost:1234/v1"
    openai_compatible_base_url: str = "http://localhost:11434/v1"
    api_key: str = "None"
    model: str = "gemma4-12b-qat-uncensored-hauhaucs-balanced"
    temperature: float = 0.75
    top_p: float = 0.9
    max_tokens: int = 1024
    max_short_term_messages: int = 20
    stop_sequences: list[str] = field(
        default_factory=lambda: [
            "\nUser:",
            "\nUSER:",
            "\nHuman:",
            "\nAssistant:",
            "<|im_end|>",
            "<|endoftext|>",
        ]
    )
    retry_empty_response: bool = True
    empty_response_retries: int = 1
    retry_token_multiplier: float = 2.0
    max_retry_tokens: int = 2048
    # LM Studio reasoning setting. Any value other than "auto" uses the
    # native /api/v1/chat endpoint so the preference is actually enforced.
    reasoning_mode: str = "off"

    # Managed llama.cpp settings. The Models page can populate the model path
    # through its download manager and tune context/offload/thread values with
    # hardware presets; every field remains manually editable in Settings.
    llama_cpp_executable: str = ""
    llama_cpp_model_path: str = ""
    llama_cpp_model_alias: str = "akira-local"
    llama_cpp_host: str = "127.0.0.1"
    llama_cpp_port: int = 8080
    llama_cpp_context_size: int = 8192
    llama_cpp_gpu_layers: str = "auto"
    llama_cpp_threads: int = -1
    llama_cpp_startup_timeout_seconds: float = 120.0
    llama_cpp_extra_args: list[str] = field(default_factory=list)


@dataclass
class PersonalitySettings:
    preset: str = "gamer"
    # Empty means use ai/personality.py's built-in gamer prompt. The future
    # WebUI can save a complete custom prompt here.
    prompt: str = ""


@dataclass
class STTSettings:
    model: str = "base"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = None
    beam_size: int = 5


@dataclass
class AudioSettings:
    recording_file: str = "input.wav"
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
    backend: str = "embedded"
    vmc_ip: str = "127.0.0.1"
    vmc_port: int = 39539
    face_blend_fps: int = 120
    mouth_fps: int = 28
    mouth_start_delay_seconds: float = 1.15
    mouth_end_delay_seconds: float = 0.05
    mouth_scale: float = 0.95
    mouth_random_amount: float = 0.08
    mouth_attack_speed: float = 0.60
    mouth_release_speed: float = 0.42
    reset_other_vowels_each_frame: bool = True
    expressions_enabled: bool = True
    idle_face_enabled: bool = True
    idle_blinks_enabled: bool = False
    idle_gaze_enabled: bool = False
    idle_face_fps: int = 20
    idle_expression_strength: float = 0.30
    speaking_expression_strength: float = 0.72
    expression_fade_speed: float = 0.08
    auto_start_idle: bool = True
    standing_pose_replay_enabled: bool = True
    pose_fps: int = 18
    body_idle_strength: float = 1.00
    body_root_bob_meters: float = 0.012
    body_sway_meters: float = 0.010
    body_breath_degrees: float = 2.20
    body_head_yaw_degrees: float = 2.40
    body_arm_sway_degrees: float = 1.80
    disable_idle_during_expressions: bool = True
    idle_strength_during_expressions: float = 0.20
    random_idle_expressions_enabled: bool = False
    body_expressions_enabled: bool = True
    body_expression_strength: float = 1.00
    body_pose_strength: float = 1.00
    body_expression_fade_speed: float = 0.055
    body_speaking_motion_boost: float = 0.28
    body_talk_pulse_degrees: float = 1.35
    body_talk_hand_meters: float = 0.010
    arm_gesture_strength: float = 1.00
    arm_tracker_strength: float = 0.00
    arm_bone_rotation_strength: float = 1.00
    arm_speaking_sway_meters: float = 0.018
    arm_speaking_lift_meters: float = 0.014
    send_tracker_positions: bool = False
    skip_eye_bones: bool = True


@dataclass
class MemorySettings:
    file: str = "data/memories.json"
    max_turns: int = 300
    max_facts: int = 200
    max_context_chars: int = 2500
    recent_turns: int = 6
    relevant_limit: int = 6


@dataclass
class AppSettings:
    """All user-configurable Project Akira settings."""

    schema_version: int = CURRENT_SCHEMA_VERSION
    general: GeneralSettings = field(default_factory=GeneralSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    personality: PersonalitySettings = field(default_factory=PersonalitySettings)
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




def _migrate_settings_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade persisted settings from older Project Akira schemas.

    Early v0.1 builds wrote ``llm.max_tokens`` as 180, then 512. Those
    values remain in ``data/settings.json`` even after the dataclass default
    changes, so users can otherwise stay stuck retrying at 360 tokens.
    """

    migrated = dict(data)
    raw_version = migrated.get("schema_version", 1)
    version = raw_version if isinstance(raw_version, int) else 1

    if version < 3:
        llm = dict(migrated.get("llm") or {})

        # Upgrade only known historical defaults. Preserve deliberately custom
        # limits such as 256, 768, or 2048.
        if llm.get("max_tokens") in (None, 180, 360, 512):
            llm["max_tokens"] = 1024

        if llm.get("reasoning_mode") in (None, "auto"):
            llm["reasoning_mode"] = "off"

        migrated["llm"] = llm

    if version < 4:
        avatar = dict(migrated.get("avatar") or {})

        # Before the embedded renderer, ``vmc`` was the only active backend.
        # Existing users were already receiving both VMC output and embedded
        # WebUI events after v0.4 work began, so preserve that behavior as
        # ``both`` rather than silently turning either output off.
        legacy_backend = avatar.get("backend")
        if legacy_backend == "vmc":
            avatar["backend"] = "both"
        elif legacy_backend not in {"embedded", "both", "disabled"}:
            avatar["backend"] = "embedded"

        migrated["avatar"] = avatar

    if version < 5:
        llm = dict(migrated.get("llm") or {})
        active_backend = str(llm.get("backend") or "lm_studio").strip().casefold()
        active_url = str(llm.get("base_url") or "").strip()
        if active_backend == "lm_studio" and active_url:
            llm.setdefault("lm_studio_base_url", active_url)
        elif active_backend in {"openai_compatible", "openai-compatible"} and active_url:
            llm.setdefault("openai_compatible_base_url", active_url)
        llm.setdefault("lm_studio_base_url", "http://localhost:1234/v1")
        llm.setdefault("openai_compatible_base_url", "http://localhost:11434/v1")
        migrated["llm"] = llm

    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    return migrated

def _from_dict(data: Any) -> AppSettings:
    if not isinstance(data, Mapping):
        return AppSettings()

    schema_version = data.get("schema_version", CURRENT_SCHEMA_VERSION)
    if not isinstance(schema_version, int):
        schema_version = CURRENT_SCHEMA_VERSION
    schema_version = max(schema_version, CURRENT_SCHEMA_VERSION)

    return AppSettings(
        schema_version=schema_version,
        general=_dataclass_from_mapping(GeneralSettings, data.get("general")),
        llm=_dataclass_from_mapping(LLMSettings, data.get("llm")),
        personality=_dataclass_from_mapping(PersonalitySettings, data.get("personality")),
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

        settings = _from_dict(_migrate_settings_data(raw))

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
