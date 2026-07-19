from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai.personality import get_personality
from app.personalities import PersonalityStore, PersonalityStoreError, get_personality_store
from config.settings import get_settings, update_settings


class PersonalityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        root = Path(self.temp_directory.name)
        self.personality_file = root / "personalities.json"
        self.settings_file = root / "settings.json"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "AKIRA_PERSONALITIES_FILE": str(self.personality_file),
                "AKIRA_SETTINGS_FILE": str(self.settings_file),
            },
        )
        self.environment.start()
        get_settings(reload=True)
        self.store = get_personality_store(reload=True)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_default_gamer_preset_is_created_and_protected(self) -> None:
        gamer = self.store.get_preset("gamer")

        self.assertIsNotNone(gamer)
        self.assertTrue(gamer.built_in)
        self.assertTrue(self.personality_file.exists())
        with self.assertRaises(PersonalityStoreError):
            self.store.update_preset("gamer", name="Changed")
        with self.assertRaises(PersonalityStoreError):
            self.store.delete_preset("gamer")

    def test_custom_preset_lifecycle(self) -> None:
        created = self.store.create_preset(
            name="Focused Builder",
            description="Practical project help",
            prompt="You are calm, concise, practical, and focused on helping with projects.",
        )
        updated = self.store.update_preset(
            created.id,
            description="Updated description",
        )
        duplicate = self.store.duplicate_preset(created.id)

        self.assertEqual(created.id, "focused-builder")
        self.assertEqual(updated.description, "Updated description")
        self.assertNotEqual(duplicate.id, created.id)
        self.assertIn("Copy", duplicate.name)

        self.store.delete_preset(created.id)
        self.assertIsNone(self.store.get_preset(created.id))
        self.assertIsNotNone(self.store.get_preset(duplicate.id))

    def test_invalid_file_is_backed_up_and_reset(self) -> None:
        self.personality_file.write_text("{not valid", encoding="utf-8")

        recovered = PersonalityStore(self.personality_file)

        self.assertIsNotNone(recovered.get_preset("gamer"))
        backups = list(self.personality_file.parent.glob("personalities.broken-*.json"))
        self.assertEqual(len(backups), 1)

    def test_selected_preset_and_legacy_override_are_respected(self) -> None:
        preset = self.store.create_preset(
            name="Supportive",
            prompt="You are Akira. Be warm, supportive, patient, and natural when speaking aloud.",
        )
        update_settings(
            {"personality": {"preset": preset.id, "prompt": ""}}
        )

        self.assertEqual(get_personality(get_settings(reload=True)), preset.prompt)

        update_settings(
            {"personality": {"prompt": "This direct prompt overrides the preset selection."}}
        )
        self.assertEqual(
            get_personality(get_settings(reload=True)),
            "This direct prompt overrides the preset selection.",
        )

    def test_saved_file_contains_only_serializable_preset_data(self) -> None:
        self.store.create_preset(
            name="Test",
            prompt="This is a sufficiently long test personality prompt for Akira.",
        )
        payload = json.loads(self.personality_file.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 1)
        self.assertGreaterEqual(len(payload["presets"]), 2)
        self.assertTrue(all("prompt" in item for item in payload["presets"]))


if __name__ == "__main__":
    unittest.main()
