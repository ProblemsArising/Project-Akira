from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.paths import resolve_runtime_paths


class RuntimePathTests(unittest.TestCase):
    def test_source_checkout_keeps_existing_project_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "app" / "paths.py"
            paths = resolve_runtime_paths(
                frozen=False,
                module_file=module,
                environment={},
            )

            self.assertEqual(paths.project_root, root.resolve())
            self.assertEqual(paths.bundle_root, root.resolve())
            self.assertEqual(paths.user_data_root, (root / "data").resolve())

    def test_frozen_build_uses_bundle_for_assets_and_local_appdata_for_data(self):
        paths = resolve_runtime_paths(
            frozen=True,
            executable=r"C:\Program Files\Project Akira\ProjectAkira.exe",
            meipass=r"C:\Program Files\Project Akira\_internal",
            environment={"LOCALAPPDATA": r"C:\Users\Akira\AppData\Local"},
            module_file=r"C:\source\Project-Akira\app\paths.py",
        )

        self.assertEqual(
            str(paths.bundle_root),
            str(Path(r"C:\Program Files\Project Akira\_internal").resolve()),
        )
        self.assertEqual(paths.user_data_root.parts[-2:], ("Project Akira", "Data"))
        self.assertTrue(paths.frozen)

    def test_explicit_data_directory_overrides_packaged_default(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "portable-data"
            paths = resolve_runtime_paths(
                frozen=True,
                executable=Path(directory) / "ProjectAkira.exe",
                meipass=Path(directory) / "_internal",
                environment={"AKIRA_DATA_DIR": str(selected)},
            )
            self.assertEqual(paths.user_data_root, selected.resolve())


class PackagingFileTests(unittest.TestCase):
    def test_spec_and_build_script_exist(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "ProjectAkira.spec").read_text(encoding="utf-8")
        build_script = (root / "build_windows.py").read_text(encoding="utf-8")

        self.assertIn('name="ProjectAkira"', spec)
        self.assertIn('ROOT / "web"', spec)
        self.assertIn('"app.api"', spec)
        self.assertIn("collect_dynamic_libs", spec)
        self.assertIn("PyInstaller", build_script)
        self.assertIn("BUILD_INFO.json", build_script)


if __name__ == "__main__":
    unittest.main()
