from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from build_installer import find_inno_compiler, safe_filename_version


class InstallerBuildTests(unittest.TestCase):
    def test_safe_filename_version(self):
        self.assertEqual(safe_filename_version(" 0.3.0 beta 1 "), "0.3.0-beta-1")
        self.assertEqual(safe_filename_version('  .<>:"/\\|?*  '), "dev")

    def test_explicit_inno_compiler_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            compiler = Path(directory) / "ISCC.exe"
            compiler.write_bytes(b"")
            self.assertEqual(
                find_inno_compiler(compiler, environment={}),
                compiler.resolve(),
            )

    def test_installer_script_contains_release_behavior(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "installer" / "ProjectAkira.iss").read_text(
            encoding="utf-8"
        )
        build_script = (root / "build_installer.py").read_text(encoding="utf-8")

        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn(r"DefaultDirName={localappdata}\Programs\Project Akira", script)
        self.assertIn(r"LicenseFile={#RepoRoot}\LICENSE", script)
        self.assertIn("recursesubdirs createallsubdirs", script)
        self.assertIn("UninstallDisplayIcon", script)
        self.assertIn("RegDeleteValue(HKCU", script)
        self.assertIn("BUILD_INFO.json", build_script)
        self.assertIn("ISCC.exe", build_script)
        self.assertIn("build_windows.py", build_script)


if __name__ == "__main__":
    unittest.main()
