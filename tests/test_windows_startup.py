from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.startup import (
    RUN_KEY_PATH,
    RUN_VALUE_NAME,
    StartupManager,
    StartupRegistrationError,
)


class FakeKey:
    def __init__(self, registry):
        self.registry = registry

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path, reserved=0, access=0):
        if path != RUN_KEY_PATH:
            raise FileNotFoundError(path)
        if not self.values and access == self.KEY_READ:
            raise FileNotFoundError(path)
        return FakeKey(self)

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        if path != RUN_KEY_PATH:
            raise FileNotFoundError(path)
        return FakeKey(self)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, value_type, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class WindowsStartupTests(unittest.TestCase):
    def source_manager(self, root: Path, registry: FakeRegistry) -> StartupManager:
        scripts = root / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        python = scripts / "python.exe"
        pythonw = scripts / "pythonw.exe"
        python.write_bytes(b"")
        pythonw.write_bytes(b"")
        (root / "desktop.py").write_text("", encoding="utf-8")
        return StartupManager(
            platform_name="nt",
            registry_module=registry,
            executable=python,
            project_root=root,
            frozen=False,
        )

    def test_source_registration_uses_pythonw_and_desktop_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.source_manager(root, FakeRegistry())

            arguments = manager.launch_arguments()
            self.assertEqual(Path(arguments[0]).name.casefold(), "pythonw.exe")
            self.assertEqual(Path(arguments[1]), root / "desktop.py")
            self.assertIn("desktop.py", manager.expected_command())

    def test_enable_and_disable_current_user_run_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = FakeRegistry()
            manager = self.source_manager(Path(directory), registry)

            enabled = manager.set_enabled(True)
            self.assertTrue(enabled.enabled)
            self.assertTrue(enabled.matches_current)
            self.assertEqual(
                registry.values[RUN_VALUE_NAME],
                manager.expected_command(),
            )

            disabled = manager.set_enabled(False)
            self.assertFalse(disabled.enabled)
            self.assertNotIn(RUN_VALUE_NAME, registry.values)

    def test_enabling_refreshes_a_stale_command(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = FakeRegistry()
            registry.values[RUN_VALUE_NAME] = '"C:\\old\\pythonw.exe" old.py'
            manager = self.source_manager(Path(directory), registry)

            stale = manager.status()
            self.assertTrue(stale.enabled)
            self.assertFalse(stale.matches_current)

            refreshed = manager.enable()
            self.assertTrue(refreshed.matches_current)

    def test_frozen_build_registers_only_packaged_executable(self):
        registry = FakeRegistry()
        manager = StartupManager(
            platform_name="nt",
            registry_module=registry,
            executable=r"C:\Program Files\Project Akira\ProjectAkira.exe",
            project_root=r"C:\unused",
            frozen=True,
        )

        arguments = manager.launch_arguments()
        self.assertEqual(len(arguments), 1)
        self.assertTrue(arguments[0].endswith("ProjectAkira.exe"))

    def test_unsupported_platform_can_disable_but_cannot_enable(self):
        manager = StartupManager(platform_name="posix")

        status = manager.disable()
        self.assertFalse(status.supported)
        self.assertFalse(status.enabled)

        with self.assertRaises(StartupRegistrationError):
            manager.enable()


if __name__ == "__main__":
    unittest.main()
