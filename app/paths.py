"""Runtime paths for source checkouts and frozen desktop builds.

Read-only assets are loaded from the source/bundle root. User-created files are
kept in the repository during development and moved to Local AppData in a
PyInstaller build so an installed application never tries to write into its
installation directory.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DATA_DIR_ENV_VAR = "AKIRA_DATA_DIR"


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    source_root: Path
    bundle_root: Path
    project_root: Path
    user_data_root: Path
    frozen: bool


def resolve_runtime_paths(
    *,
    frozen: bool | None = None,
    executable: str | Path | None = None,
    meipass: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    module_file: str | Path = __file__,
    home: str | Path | None = None,
) -> RuntimePaths:
    """Resolve resource and writable-data locations without touching disk."""

    environment = os.environ if environment is None else environment
    source_root = Path(module_file).resolve().parents[1]
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)

    if is_frozen:
        bundle_root = Path(
            meipass or getattr(sys, "_MEIPASS", Path(executable or sys.executable).parent)
        ).resolve()
        configured = str(environment.get(DATA_DIR_ENV_VAR, "")).strip()
        if configured:
            user_data_root = Path(configured).expanduser().resolve()
        else:
            local_app_data = str(environment.get("LOCALAPPDATA", "")).strip()
            if local_app_data:
                application_root = Path(local_app_data).expanduser() / "Project Akira"
            else:
                application_root = Path(home or Path.home()) / ".project-akira"
            user_data_root = application_root / "Data"
        project_root = user_data_root.parent
    else:
        bundle_root = source_root
        project_root = source_root
        user_data_root = source_root / "data"

    return RuntimePaths(
        source_root=source_root,
        bundle_root=bundle_root,
        project_root=project_root,
        user_data_root=user_data_root,
        frozen=is_frozen,
    )


RUNTIME_PATHS = resolve_runtime_paths()
SOURCE_ROOT = RUNTIME_PATHS.source_root
BUNDLE_ROOT = RUNTIME_PATHS.bundle_root
PROJECT_ROOT = RUNTIME_PATHS.project_root
USER_DATA_ROOT = RUNTIME_PATHS.user_data_root
IS_FROZEN = RUNTIME_PATHS.frozen


def resource_path(*parts: str | Path) -> Path:
    """Return a read-only source/bundled resource path."""

    return BUNDLE_ROOT.joinpath(*(Path(part) for part in parts))


def user_file_path(path: str | Path) -> Path:
    """Resolve a user-writable file while preserving source-mode behavior."""

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    if IS_FROZEN:
        parts = candidate.parts
        if parts and parts[0].casefold() == "data":
            parts = parts[1:]
        return USER_DATA_ROOT.joinpath(*parts).resolve()

    return PROJECT_ROOT.joinpath(candidate).resolve()


__all__ = [
    "BUNDLE_ROOT",
    "DATA_DIR_ENV_VAR",
    "IS_FROZEN",
    "PROJECT_ROOT",
    "RUNTIME_PATHS",
    "RuntimePaths",
    "SOURCE_ROOT",
    "USER_DATA_ROOT",
    "resolve_runtime_paths",
    "resource_path",
    "user_file_path",
]
