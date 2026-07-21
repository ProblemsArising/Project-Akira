"""Standard-stream compatibility for windowed desktop builds."""

from __future__ import annotations

import os
import sys

from typing import TextIO


def prepare_standard_stream(stream: TextIO | None) -> TextIO:
    """Return a writable UTF-8 stream for diagnostic output."""

    if stream is None:
        return open(
            os.devnull,
            mode="w",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )

    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    return stream


def configure_standard_streams() -> None:
    """Make stdout and stderr safe in PyInstaller windowed builds."""

    sys.stdout = prepare_standard_stream(sys.stdout)
    sys.stderr = prepare_standard_stream(sys.stderr)
