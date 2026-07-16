"""Project Akira configuration package.

Exports are loaded lazily so ``python -m config.settings`` can run without
importing the module twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import AppSettings

__all__ = [
    "AppSettings",
    "get_settings",
    "load_settings",
    "reload_settings",
    "reset_settings",
    "save_settings",
    "update_settings",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import settings

        return getattr(settings, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
