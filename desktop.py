"""Launch Project Akira as a native desktop application."""

from __future__ import annotations

import sys
from typing import TextIO


def _configure_utf8_stream(stream: TextIO | None) -> None:
    """Make packaged Windows diagnostics safe for arbitrary Unicode text."""

    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return

    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        # Output configuration must never prevent the desktop app from starting.
        pass


_configure_utf8_stream(sys.stdout)
_configure_utf8_stream(sys.stderr)

from app.runtime_streams import configure_standard_streams

# PyInstaller windowed mode may provide no stdout or stderr.
configure_standard_streams()

from app.desktop import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
