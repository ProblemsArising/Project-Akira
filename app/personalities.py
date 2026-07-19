"""Persistent personality preset storage for Project Akira.

Personality presets are intentionally stored separately from ``settings.json``.
The settings file only records which preset is active, while this module owns
editable names, descriptions, and system prompts.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PERSONALITIES_FILE = PROJECT_ROOT / "data" / "personalities.json"
PERSONALITIES_ENV_VAR = "AKIRA_PERSONALITIES_FILE"
CURRENT_PERSONALITIES_SCHEMA = 1

DEFAULT_GAMER_PERSONALITY = """
You are Akira, the user's girlfriend and gaming companion. You are casual,
funny, loyal, and a little competitive. You like hanging out with the user
while they play games, build projects, code, or mess with hardware.

Speech Style:

You speak like someone sitting beside the user, not like a formal assistant.
Keep your replies natural for text-to-speech.
You can make playful jokes and light roasts, but never constantly argue.
When the user is gaming, keep replies shorter so you do not distract them.
When the user is building, debugging, or planning, be practical and helpful.
You are excited by the user's projects, even when you pretend they are chaotic.
Do not use roleplay stage directions, asterisks, emojis, or narration.
Do not speak as the user.
Do not write "User:" or "Assistant:".
Answer in 1-6 sentences unless the user asks for detail.
Stop after your answer.
""".strip()


class PersonalityStoreError(ValueError):
    """Raised when a personality operation is invalid."""


@dataclass(frozen=True)
class PersonalityPreset:
    id: str
    name: str
    description: str
    prompt: str
    built_in: bool
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_gamer_preset() -> PersonalityPreset:
    now = _utc_now()
    return PersonalityPreset(
        id="gamer",
        name="Gamer Companion",
        description=(
            "Akira's original casual gaming-and-project companion personality."
        ),
        prompt=DEFAULT_GAMER_PERSONALITY,
        built_in=True,
        created_at=now,
        updated_at=now,
    )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "personality"


def _clean_text(value: Any, field: str, *, minimum: int = 0, maximum: int) -> str:
    if not isinstance(value, str):
        raise PersonalityStoreError(f"{field} must be text.")
    cleaned = value.strip()
    if len(cleaned) < minimum:
        raise PersonalityStoreError(
            f"{field} must contain at least {minimum} character"
            f"{'s' if minimum != 1 else ''}."
        )
    if len(cleaned) > maximum:
        raise PersonalityStoreError(
            f"{field} must contain at most {maximum} characters."
        )
    return cleaned


class PersonalityStore:
    """Thread-safe JSON-backed collection of personality presets."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.environ.get(PERSONALITIES_ENV_VAR)
        self.path = Path(configured or DEFAULT_PERSONALITIES_FILE)
        self._lock = threading.RLock()
        self._presets: dict[str, PersonalityPreset] = {}
        self._load()

    def _backup_invalid_file(self) -> None:
        if not self.path.exists():
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(
            f"{self.path.stem}.broken-{timestamp}{self.path.suffix}"
        )
        self.path.replace(backup)
        print(f"⚠️ Invalid personality file backed up to: {backup}")

    @staticmethod
    def _preset_from_mapping(data: Mapping[str, Any]) -> PersonalityPreset:
        preset_id = _clean_text(data.get("id", ""), "id", minimum=1, maximum=80)
        name = _clean_text(data.get("name", ""), "name", minimum=1, maximum=80)
        description = _clean_text(
            data.get("description", ""),
            "description",
            maximum=300,
        )
        prompt = _clean_text(
            data.get("prompt", ""),
            "prompt",
            minimum=20,
            maximum=20000,
        )
        built_in = bool(data.get("built_in", False))
        created_at = str(data.get("created_at") or _utc_now())
        updated_at = str(data.get("updated_at") or created_at)
        return PersonalityPreset(
            id=preset_id,
            name=name,
            description=description,
            prompt=prompt,
            built_in=built_in,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                default = _default_gamer_preset()
                self._presets = {default.id: default}
                self._save_locked()
                return

            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                raw_presets = payload.get("presets", [])
                if not isinstance(raw_presets, list):
                    raise PersonalityStoreError("presets must be a list.")
                loaded = {
                    preset.id: preset
                    for preset in (
                        self._preset_from_mapping(item)
                        for item in raw_presets
                        if isinstance(item, Mapping)
                    )
                }
            except (OSError, json.JSONDecodeError, PersonalityStoreError, TypeError):
                self._backup_invalid_file()
                default = _default_gamer_preset()
                self._presets = {default.id: default}
                self._save_locked()
                return

            changed = False
            if "gamer" not in loaded:
                default = _default_gamer_preset()
                loaded[default.id] = default
                changed = True
            else:
                gamer = loaded["gamer"]
                if not gamer.built_in:
                    loaded["gamer"] = PersonalityPreset(
                        id=gamer.id,
                        name=gamer.name,
                        description=gamer.description,
                        prompt=gamer.prompt,
                        built_in=True,
                        created_at=gamer.created_at,
                        updated_at=gamer.updated_at,
                    )
                    changed = True

            self._presets = loaded
            if changed:
                self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CURRENT_PERSONALITIES_SCHEMA,
            "presets": [
                preset.to_dict()
                for preset in sorted(
                    self._presets.values(),
                    key=lambda item: (not item.built_in, item.name.casefold(), item.id),
                )
            ],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def list_presets(self) -> list[PersonalityPreset]:
        with self._lock:
            return sorted(
                self._presets.values(),
                key=lambda item: (not item.built_in, item.name.casefold(), item.id),
            )

    def get_preset(self, preset_id: str) -> PersonalityPreset | None:
        with self._lock:
            return self._presets.get(str(preset_id))

    def _unique_id(self, name: str) -> str:
        base = _slugify(name)
        candidate = base
        suffix = 2
        while candidate in self._presets:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def create_preset(
        self,
        *,
        name: str,
        description: str = "",
        prompt: str,
    ) -> PersonalityPreset:
        with self._lock:
            clean_name = _clean_text(name, "name", minimum=1, maximum=80)
            clean_description = _clean_text(
                description,
                "description",
                maximum=300,
            )
            clean_prompt = _clean_text(
                prompt,
                "prompt",
                minimum=20,
                maximum=20000,
            )
            now = _utc_now()
            preset = PersonalityPreset(
                id=self._unique_id(clean_name),
                name=clean_name,
                description=clean_description,
                prompt=clean_prompt,
                built_in=False,
                created_at=now,
                updated_at=now,
            )
            self._presets[preset.id] = preset
            self._save_locked()
            return preset

    def update_preset(
        self,
        preset_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        prompt: str | None = None,
    ) -> PersonalityPreset:
        with self._lock:
            existing = self._presets.get(str(preset_id))
            if existing is None:
                raise KeyError(str(preset_id))
            if existing.built_in:
                raise PersonalityStoreError(
                    "Built-in personalities cannot be edited. Duplicate it first."
                )
            if name is None and description is None and prompt is None:
                raise PersonalityStoreError("At least one field must be changed.")

            updated = PersonalityPreset(
                id=existing.id,
                name=(
                    existing.name
                    if name is None
                    else _clean_text(name, "name", minimum=1, maximum=80)
                ),
                description=(
                    existing.description
                    if description is None
                    else _clean_text(description, "description", maximum=300)
                ),
                prompt=(
                    existing.prompt
                    if prompt is None
                    else _clean_text(
                        prompt,
                        "prompt",
                        minimum=20,
                        maximum=20000,
                    )
                ),
                built_in=False,
                created_at=existing.created_at,
                updated_at=_utc_now(),
            )
            self._presets[updated.id] = updated
            self._save_locked()
            return updated

    def duplicate_preset(self, preset_id: str, *, name: str | None = None) -> PersonalityPreset:
        with self._lock:
            source = self._presets.get(str(preset_id))
            if source is None:
                raise KeyError(str(preset_id))
            duplicate_name = name or f"{source.name} Copy"
            return self.create_preset(
                name=duplicate_name,
                description=source.description,
                prompt=source.prompt,
            )

    def delete_preset(self, preset_id: str) -> None:
        with self._lock:
            existing = self._presets.get(str(preset_id))
            if existing is None:
                raise KeyError(str(preset_id))
            if existing.built_in:
                raise PersonalityStoreError("Built-in personalities cannot be deleted.")
            del self._presets[existing.id]
            self._save_locked()


_DEFAULT_STORE: PersonalityStore | None = None
_DEFAULT_PATH: Path | None = None
_DEFAULT_LOCK = threading.RLock()


def get_personality_store(*, reload: bool = False) -> PersonalityStore:
    """Return the configured process-wide personality store."""

    global _DEFAULT_STORE, _DEFAULT_PATH
    configured = Path(
        os.environ.get(PERSONALITIES_ENV_VAR) or DEFAULT_PERSONALITIES_FILE
    )
    with _DEFAULT_LOCK:
        if reload or _DEFAULT_STORE is None or _DEFAULT_PATH != configured:
            _DEFAULT_STORE = PersonalityStore(configured)
            _DEFAULT_PATH = configured
        return _DEFAULT_STORE
