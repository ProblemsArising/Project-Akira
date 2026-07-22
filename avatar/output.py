"""Avatar output backend selection helpers.

The embedded renderer is Project Akira's primary avatar output. VMC remains an
optional compatibility target for VSeeFace and other VMC receivers. Keeping the
selection logic here prevents the Python VMC controller, settings migration,
and tests from interpreting backend names differently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AVATAR_OUTPUT_BACKENDS = ("embedded", "vmc", "both", "disabled")


@dataclass(frozen=True, slots=True)
class AvatarOutputSelection:
    """Resolved avatar outputs for one settings object."""

    backend: str
    enabled: bool
    embedded: bool
    vmc: bool


def normalize_avatar_backend(value: object) -> str:
    """Return a supported backend name, falling back to embedded output."""

    normalized = str(value or "").strip().casefold()
    if normalized in AVATAR_OUTPUT_BACKENDS:
        return normalized
    return "embedded"


def resolve_avatar_outputs(settings: Any) -> AvatarOutputSelection:
    """Resolve embedded and VMC targets from ``AvatarSettings``-like data."""

    enabled = bool(getattr(settings, "enabled", True))
    backend = normalize_avatar_backend(getattr(settings, "backend", "embedded"))
    return AvatarOutputSelection(
        backend=backend,
        enabled=enabled,
        embedded=enabled and backend in {"embedded", "both"},
        vmc=enabled and backend in {"vmc", "both"},
    )


def embedded_output_enabled(settings: Any) -> bool:
    return resolve_avatar_outputs(settings).embedded


def vmc_output_enabled(settings: Any) -> bool:
    return resolve_avatar_outputs(settings).vmc
