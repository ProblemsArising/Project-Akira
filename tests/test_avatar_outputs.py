from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from avatar.output import (
    embedded_output_enabled,
    normalize_avatar_backend,
    resolve_avatar_outputs,
    vmc_output_enabled,
)
from config.settings import AppSettings, load_settings


class AvatarOutputSelectionTests(unittest.TestCase):
    def test_each_backend_resolves_expected_targets(self) -> None:
        expected = {
            "embedded": (True, False),
            "vmc": (False, True),
            "both": (True, True),
            "disabled": (False, False),
        }
        for backend, targets in expected.items():
            with self.subTest(backend=backend):
                settings = SimpleNamespace(enabled=True, backend=backend)
                resolved = resolve_avatar_outputs(settings)
                self.assertEqual((resolved.embedded, resolved.vmc), targets)
                self.assertEqual(embedded_output_enabled(settings), targets[0])
                self.assertEqual(vmc_output_enabled(settings), targets[1])

    def test_global_enabled_flag_disables_every_target(self) -> None:
        resolved = resolve_avatar_outputs(
            SimpleNamespace(enabled=False, backend="both")
        )
        self.assertFalse(resolved.embedded)
        self.assertFalse(resolved.vmc)

    def test_unknown_backend_falls_back_to_embedded(self) -> None:
        self.assertEqual(normalize_avatar_backend("unknown"), "embedded")
        self.assertEqual(normalize_avatar_backend(None), "embedded")

    def test_fresh_settings_default_to_embedded_output(self) -> None:
        self.assertEqual(AppSettings().avatar.backend, "embedded")

    def test_legacy_vmc_setting_migrates_to_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            settings_file.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "avatar": {"enabled": True, "backend": "vmc"},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"AKIRA_SETTINGS_FILE": str(settings_file)},
            ):
                settings = load_settings(settings_file)

            self.assertEqual(settings.schema_version, 4)
            self.assertEqual(settings.avatar.backend, "both")


class AvatarOutputAssetTests(unittest.TestCase):
    def test_embedded_avatar_uses_shared_output_mode_helper(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app = (root / "web" / "avatar" / "app.js").read_text(encoding="utf-8")
        output = (root / "web" / "avatar" / "output.js").read_text(encoding="utf-8")

        self.assertIn('/static/avatar/output.js', app)
        self.assertIn('resolveAvatarOutputs', app)
        self.assertIn('if (!state.output.embedded)', app)
        self.assertIn('backend === "both"', output)
        self.assertIn('backend === "vmc"', output)

    def test_settings_page_exposes_all_output_modes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "web" / "settings" / "app.js").read_text(encoding="utf-8")

        for value in ("embedded", "both", "vmc", "disabled"):
            with self.subTest(value=value):
                self.assertIn(f'value: "{value}"', script)


if __name__ == "__main__":
    unittest.main()
