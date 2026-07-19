"""Validation for partial settings updates received from the local WebUI."""

from __future__ import annotations

from typing import Any, Mapping


class SettingsValidationError(ValueError):
    """Raised when a settings update contains an unknown or invalid value."""


_EDITABLE_SECTIONS = {
    "general",
    "llm",
    "personality",
    "stt",
    "audio",
    "tts",
    "avatar",
    "memory",
}

_NULLABLE_TYPES: dict[tuple[str, str], tuple[type, ...]] = {
    ("stt", "language"): (str, type(None)),
    ("audio", "input_device"): (str, int, type(None)),
    ("audio", "output_device"): (str, int, type(None)),
}

_ENUMS: dict[tuple[str, str], set[str]] = {
    ("llm", "reasoning_mode"): {"off", "auto", "low", "medium", "high", "on"},
    ("stt", "device"): {"auto", "cpu", "cuda"},
    ("stt", "compute_type"): {
        "auto",
        "default",
        "float16",
        "float32",
        "int8",
        "int8_float16",
        "int8_float32",
    },
    ("avatar", "backend"): {"vmc", "disabled"},
}

_RANGES: dict[tuple[str, str], tuple[float | None, float | None]] = {
    ("llm", "temperature"): (0.0, 2.0),
    ("llm", "top_p"): (0.0, 1.0),
    ("llm", "max_tokens"): (1, 32768),
    ("llm", "max_short_term_messages"): (1, 500),
    ("llm", "empty_response_retries"): (0, 10),
    ("llm", "retry_token_multiplier"): (1.0, 10.0),
    ("llm", "max_retry_tokens"): (1, 65536),
    ("stt", "beam_size"): (1, 50),
    ("audio", "sample_rate"): (8000, 384000),
    ("audio", "channels"): (1, 8),
    ("audio", "frame_ms"): (10, 100),
    ("audio", "pre_roll_seconds"): (0.0, 10.0),
    ("audio", "end_silence_seconds"): (0.05, 30.0),
    ("audio", "min_record_seconds"): (0.0, 30.0),
    ("audio", "max_record_seconds"): (1.0, 600.0),
    ("audio", "calibration_seconds"): (0.0, 30.0),
    ("audio", "start_threshold_multiplier"): (0.1, 100.0),
    ("audio", "end_threshold_multiplier"): (0.1, 100.0),
    ("audio", "min_start_threshold"): (0.0, 1.0),
    ("audio", "min_end_threshold"): (0.0, 1.0),
    ("tts", "voice_index"): (0, 100),
    ("tts", "rate"): (50, 500),
    ("tts", "volume"): (0.0, 1.0),
    ("avatar", "vmc_port"): (1, 65535),
    ("avatar", "face_blend_fps"): (1, 240),
    ("avatar", "mouth_fps"): (1, 240),
    ("avatar", "idle_face_fps"): (1, 240),
    ("avatar", "pose_fps"): (1, 240),
    ("memory", "max_turns"): (1, 100000),
    ("memory", "max_facts"): (1, 100000),
    ("memory", "max_context_chars"): (100, 1000000),
    ("memory", "recent_turns"): (0, 1000),
    ("memory", "relevant_limit"): (0, 1000),
}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _normalize_value(section: str, field: str, value: Any, current: Any) -> Any:
    path = (section, field)

    nullable_types = _NULLABLE_TYPES.get(path)
    if nullable_types is not None:
        if isinstance(value, bool) or not isinstance(value, nullable_types):
            allowed = ", ".join(item.__name__ for item in nullable_types)
            raise SettingsValidationError(
                f"{section}.{field} must be one of: {allowed}."
            )
        normalized = value
    elif isinstance(current, bool):
        if not isinstance(value, bool):
            raise SettingsValidationError(f"{section}.{field} must be true or false.")
        normalized = value
    elif isinstance(current, int) and not isinstance(current, bool):
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsValidationError(f"{section}.{field} must be an integer.")
        normalized = value
    elif isinstance(current, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError(f"{section}.{field} must be a number.")
        normalized = float(value)
    elif isinstance(current, str):
        if not isinstance(value, str):
            raise SettingsValidationError(f"{section}.{field} must be text.")
        normalized = value
    elif isinstance(current, list):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SettingsValidationError(
                f"{section}.{field} must be a list of text values."
            )
        normalized = list(value)
    else:
        raise SettingsValidationError(
            f"{section}.{field} has unsupported type {_type_name(current)}."
        )

    allowed_values = _ENUMS.get(path)
    if allowed_values is not None and normalized not in allowed_values:
        choices = ", ".join(sorted(allowed_values))
        raise SettingsValidationError(
            f"{section}.{field} must be one of: {choices}."
        )

    bounds = _RANGES.get(path)
    if bounds is not None and normalized is not None:
        minimum, maximum = bounds
        if minimum is not None and normalized < minimum:
            raise SettingsValidationError(
                f"{section}.{field} must be at least {minimum}."
            )
        if maximum is not None and normalized > maximum:
            raise SettingsValidationError(
                f"{section}.{field} must be at most {maximum}."
            )

    return normalized


def validate_settings_changes(
    changes: Mapping[str, Any],
    current_settings: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate and normalize a partial settings mapping.

    Only known section/field pairs are accepted. This prevents a typo in the
    browser UI from silently writing unusable values to ``settings.json``.
    """

    if not isinstance(changes, Mapping) or not changes:
        raise SettingsValidationError("At least one setting must be changed.")

    normalized: dict[str, dict[str, Any]] = {}
    for section, section_changes in changes.items():
        if section not in _EDITABLE_SECTIONS:
            raise SettingsValidationError(f"Unknown or read-only settings section: {section}.")
        if not isinstance(section_changes, Mapping) or not section_changes:
            raise SettingsValidationError(f"{section} must contain at least one setting.")

        current_section = current_settings.get(section)
        if not isinstance(current_section, Mapping):
            raise SettingsValidationError(f"Settings section is unavailable: {section}.")

        output_section: dict[str, Any] = {}
        for field, value in section_changes.items():
            if field not in current_section:
                raise SettingsValidationError(f"Unknown setting: {section}.{field}.")
            output_section[field] = _normalize_value(
                section,
                field,
                value,
                current_section[field],
            )
        normalized[section] = output_section

    return normalized
