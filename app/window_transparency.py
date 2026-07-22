"""Best-effort native transparency support for the avatar window.

pywebview supports transparent client areas on several desktop backends. Its
Edge/Chromium provides the transparent WebView surface through pywebview.
Project Akira additionally applies a WinForms chroma key so the transparent
surface reveals windows behind the avatar rather than the Form background.
All native integration is optional: failure falls back to the normal opaque
window instead of preventing the desktop application from starting.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

WINDOWS_TRANSPARENCY_KEY = "#010001"
ColorFactory = Callable[[int, int, int, int], Any]


def _default_color_factory(alpha: int, red: int, green: int, blue: int) -> Any:
    from System.Drawing import Color  # type: ignore[import-not-found]

    return Color.FromArgb(alpha, red, green, blue)


def apply_windows_avatar_transparency(
    window: Any,
    *,
    platform_name: str | None = None,
    color_factory: ColorFactory | None = None,
) -> bool:
    """Apply a chroma-keyed transparent client area to a WinForms window."""

    platform = os.name if platform_name is None else str(platform_name)
    if platform != "nt":
        return False

    native = getattr(window, "native", None)
    if native is None:
        return False

    factory = color_factory or _default_color_factory
    try:
        key = factory(255, 1, 0, 1)

        # pywebview owns WebView2's transparent backing surface when the window
        # is created with transparent=True. This helper only makes the native
        # Form's keyed background reveal the desktop underneath.
        native.AllowTransparency = True
        native.BackColor = key
        native.TransparencyKey = key

        return True
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return False
