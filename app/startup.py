"""Windows launch-at-login registration for Project Akira.

The integration uses the current user's ``Run`` registry key, so enabling it
does not require administrator privileges. Source checkouts launch through the
active Python environment's ``pythonw.exe``. Frozen/PyInstaller builds register
their packaged executable directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "Project Akira"


class StartupRegistrationError(RuntimeError):
    """Raised when Windows startup registration cannot be changed."""


@dataclass(frozen=True)
class StartupStatus:
    """Current Windows startup-registration state."""

    supported: bool
    enabled: bool
    registered_command: str | None
    expected_command: str
    matches_current: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "enabled": self.enabled,
            "registered_command": self.registered_command,
            "expected_command": self.expected_command,
            "matches_current": self.matches_current,
        }


class StartupManager:
    """Read and update Project Akira's per-user Windows startup entry."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        registry_module: Any | None = None,
        executable: str | Path | None = None,
        project_root: str | Path = PROJECT_ROOT,
        frozen: bool | None = None,
    ) -> None:
        self.platform_name = str(platform_name or os.name)
        self._registry_module = registry_module
        self.executable = Path(executable or sys.executable).resolve()
        self.project_root = Path(project_root).resolve()
        self.frozen = (
            bool(getattr(sys, "frozen", False))
            if frozen is None
            else bool(frozen)
        )
        self._lock = threading.RLock()

    @property
    def supported(self) -> bool:
        return self.platform_name == "nt"

    def _registry(self) -> Any:
        if self._registry_module is not None:
            return self._registry_module

        if not self.supported:
            raise StartupRegistrationError(
                "Launch at Windows startup is available only on Windows."
            )

        try:
            import winreg
        except ImportError as error:
            raise StartupRegistrationError(
                "The Windows registry module is unavailable."
            ) from error

        self._registry_module = winreg
        return winreg

    def launch_arguments(self) -> list[str]:
        """Return the executable/arguments Windows should launch."""

        if self.frozen:
            return [str(self.executable)]

        python_executable = self.executable
        pythonw = python_executable.with_name("pythonw.exe")
        if pythonw.exists():
            python_executable = pythonw

        desktop_script = self.project_root / "desktop.py"
        return [str(python_executable), str(desktop_script)]

    def expected_command(self) -> str:
        """Return a correctly quoted Windows command line."""

        return subprocess.list2cmdline(self.launch_arguments())

    @staticmethod
    def _commands_match(left: str | None, right: str | None) -> bool:
        if left is None or right is None:
            return False
        return left.strip().casefold() == right.strip().casefold()

    def registered_command(self) -> str | None:
        """Read the current user's Project Akira Run entry."""

        if not self.supported:
            return None

        registry = self._registry()
        try:
            with registry.OpenKey(
                registry.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                registry.KEY_READ,
            ) as key:
                value, _value_type = registry.QueryValueEx(
                    key,
                    RUN_VALUE_NAME,
                )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StartupRegistrationError(
                f"Could not read the Windows startup entry: {error}"
            ) from error

        command = str(value).strip()
        return command or None

    def status(self) -> StartupStatus:
        expected = self.expected_command()
        registered = self.registered_command()
        return StartupStatus(
            supported=self.supported,
            enabled=registered is not None,
            registered_command=registered,
            expected_command=expected,
            matches_current=self._commands_match(registered, expected),
        )

    def enable(self) -> StartupStatus:
        """Create or refresh the current-user startup entry."""

        if not self.supported:
            raise StartupRegistrationError(
                "Launch at Windows startup is available only on Windows."
            )

        registry = self._registry()
        command = self.expected_command()

        try:
            with registry.CreateKeyEx(
                registry.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                registry.KEY_SET_VALUE,
            ) as key:
                registry.SetValueEx(
                    key,
                    RUN_VALUE_NAME,
                    0,
                    registry.REG_SZ,
                    command,
                )
        except OSError as error:
            raise StartupRegistrationError(
                f"Could not enable Windows startup: {error}"
            ) from error

        return self.status()

    def disable(self) -> StartupStatus:
        """Remove the current-user startup entry if it exists."""

        if not self.supported:
            return self.status()

        registry = self._registry()
        try:
            with registry.OpenKey(
                registry.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                registry.KEY_SET_VALUE,
            ) as key:
                registry.DeleteValue(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise StartupRegistrationError(
                f"Could not disable Windows startup: {error}"
            ) from error

        return self.status()

    def set_enabled(self, enabled: bool) -> StartupStatus:
        """Apply the requested startup state."""

        with self._lock:
            return self.enable() if bool(enabled) else self.disable()


@lru_cache(maxsize=1)
def get_startup_manager() -> StartupManager:
    """Return the process-wide startup manager."""

    return StartupManager()


def clear_startup_manager_cache() -> None:
    """Clear the cached manager for tests or environment changes."""

    get_startup_manager.cache_clear()


__all__ = [
    "PROJECT_ROOT",
    "RUN_KEY_PATH",
    "RUN_VALUE_NAME",
    "StartupManager",
    "StartupRegistrationError",
    "StartupStatus",
    "clear_startup_manager_cache",
    "get_startup_manager",
]
