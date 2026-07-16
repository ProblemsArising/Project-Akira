from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.settings import (
    AppSettings,
    get_settings,
    load_settings,
    reset_settings,
    save_settings,
    update_settings,
)


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.settings_file = Path(self.temp_directory.name) / "settings.json"
        self.environment = patch.dict(
            os.environ,
            {"AKIRA_SETTINGS_FILE": str(self.settings_file)},
        )
        self.environment.start()
        get_settings(reload=True)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_missing_file_is_created_with_defaults(self) -> None:
        settings = reset_settings()

        self.assertTrue(self.settings_file.exists())
        self.assertEqual(settings.llm.backend, "lm_studio")
        self.assertEqual(settings.avatar.face_blend_fps, 120)

    def test_nested_updates_are_persisted(self) -> None:
        update_settings(
            {
                "audio": {"end_silence_seconds": 1.25},
                "stt": {"device": "cpu", "compute_type": "int8"},
            }
        )

        loaded = load_settings(self.settings_file)
        self.assertEqual(loaded.audio.end_silence_seconds, 1.25)
        self.assertEqual(loaded.stt.device, "cpu")
        self.assertEqual(loaded.stt.compute_type, "int8")
        self.assertEqual(loaded.llm.model, AppSettings().llm.model)

    def test_missing_and_unknown_fields_are_handled(self) -> None:
        self.settings_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "llm": {"model": "test-model", "future_setting": True},
                }
            ),
            encoding="utf-8",
        )

        loaded = load_settings(self.settings_file)
        self.assertEqual(loaded.llm.model, "test-model")
        self.assertEqual(loaded.audio.sample_rate, 16000)

        saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
        self.assertNotIn("future_setting", saved["llm"])
        self.assertIn("audio", saved)

    def test_invalid_json_is_backed_up_and_reset(self) -> None:
        self.settings_file.write_text("{ definitely not json", encoding="utf-8")

        loaded = load_settings(self.settings_file)

        self.assertEqual(loaded, AppSettings())
        backups = list(self.settings_file.parent.glob("settings.broken-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertTrue(self.settings_file.exists())

    def test_atomic_save_leaves_no_temporary_file(self) -> None:
        save_settings(AppSettings(), self.settings_file)

        self.assertTrue(self.settings_file.exists())
        self.assertFalse(self.settings_file.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
