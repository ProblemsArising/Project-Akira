"""Personality prompt selection for Project Akira."""

from __future__ import annotations

from app.personalities import DEFAULT_GAMER_PERSONALITY, get_personality_store
from config.settings import AppSettings, get_settings


def get_personality(settings: AppSettings | None = None) -> str:
    """Return the active personality prompt.

    ``personality.prompt`` remains a backward-compatible direct override for
    older settings files. When it is empty, ``personality.preset`` selects an
    editable preset from ``data/personalities.json``.
    """

    active = settings or get_settings()
    custom = str(active.personality.prompt or "").strip()
    if custom:
        return custom

    store = get_personality_store()
    selected = store.get_preset(str(active.personality.preset or "gamer"))
    if selected is None:
        selected = store.get_preset("gamer")
    return selected.prompt if selected is not None else DEFAULT_GAMER_PERSONALITY


# Backward-compatible name used by the original project.
def getPersonality() -> str:
    return get_personality()
