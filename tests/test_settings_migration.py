from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.settings import AppSettings, load_settings


class SettingsMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.settings_file = Path(self.temp_directory.name) / "settings.json"
        self.environment = patch.dict(
            os.environ,
            {"AKIRA_SETTINGS_FILE": str(self.settings_file)},
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_new_defaults_use_larger_budget_and_reasoning_off(self) -> None:
        defaults = AppSettings()
        self.assertEqual(defaults.llm.max_tokens, 1024)
        self.assertEqual(defaults.llm.reasoning_mode, "off")
        self.assertEqual(defaults.schema_version, 4)

    def test_old_generated_defaults_are_upgraded(self) -> None:
        self.settings_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "llm": {
                        "max_tokens": 180,
                        "reasoning_mode": "auto",
                    },
                }
            ),
            encoding="utf-8",
        )

        settings = load_settings(self.settings_file)

        self.assertEqual(settings.llm.max_tokens, 1024)
        self.assertEqual(settings.llm.reasoning_mode, "off")
        self.assertEqual(settings.schema_version, 4)

        saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["max_tokens"], 1024)
        self.assertEqual(saved["llm"]["reasoning_mode"], "off")
        self.assertEqual(saved["schema_version"], 4)

    def test_custom_token_limit_is_preserved(self) -> None:
        self.settings_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "llm": {
                        "max_tokens": 768,
                        "reasoning_mode": "low",
                    },
                }
            ),
            encoding="utf-8",
        )

        settings = load_settings(self.settings_file)

        self.assertEqual(settings.llm.max_tokens, 768)
        self.assertEqual(settings.llm.reasoning_mode, "low")
        self.assertEqual(settings.schema_version, 4)


if __name__ == "__main__":
    unittest.main()
