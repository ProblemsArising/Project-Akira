from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.history import ChatHistoryStore
from app.personalities import PersonalityStore
from config.settings import get_settings


class PersonalityPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        root = Path(self.temp_directory.name)
        self.settings_file = root / "settings.json"
        self.personality_file = root / "personalities.json"
        self.history_file = root / "history.db"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "AKIRA_SETTINGS_FILE": str(self.settings_file),
                "AKIRA_PERSONALITIES_FILE": str(self.personality_file),
                "AKIRA_HISTORY_FILE": str(self.history_file),
            },
        )
        self.environment.start()
        get_settings(reload=True)

        self.service_factory_calls = 0

        def service_factory():
            self.service_factory_calls += 1
            raise AssertionError("Personality management must not load heavy services")

        self.runtime = BackendRuntime(
            service_factory=service_factory,
            history_factory=lambda: ChatHistoryStore(self.history_file),
            personality_factory=lambda: PersonalityStore(self.personality_file),
        )
        self.refresh_patch = mock.patch(
            "app.api._refresh_personality_prompt",
            return_value=True,
        )
        self.refresh_personality = self.refresh_patch.start()
        self.app = create_app(self.runtime)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.refresh_patch.stop()
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_page_and_assets_do_not_load_conversation_service(self) -> None:
        page = self.client.get("/personalities")
        stylesheet = self.client.get("/static/personalities/styles.css")
        script = self.client.get("/static/personalities/app.js")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Personality editor", page.text)
        self.assertIn("/api/personalities", script.text)
        self.assertIn('id="cancelDeleteButton"', page.text)
        self.assertIn('id="closeDeleteButton"', page.text)
        self.assertIn("closeDeleteDialog", script.text)
        self.assertIn(".personality-layout", stylesheet.text)
        self.assertEqual(self.service_factory_calls, 0)

    def test_create_update_duplicate_activate_and_delete(self) -> None:
        initial = self.client.get("/api/personalities")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["active_id"], "gamer")
        self.assertEqual(initial.json()["presets"][0]["id"], "gamer")

        created = self.client.post(
            "/api/personalities",
            json={
                "name": "Focused Builder",
                "description": "Project mode",
                "prompt": "You are focused, practical, concise, and helpful with technical projects.",
                "activate": False,
            },
        )
        self.assertEqual(created.status_code, 201)
        preset_id = created.json()["preset"]["id"]

        updated = self.client.patch(
            f"/api/personalities/{preset_id}",
            json={"description": "Updated project mode"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["preset"]["description"], "Updated project mode")

        duplicated = self.client.post(
            f"/api/personalities/{preset_id}/duplicate",
            json={"name": "Focused Copy", "activate": False},
        )
        self.assertEqual(duplicated.status_code, 201)
        duplicate_id = duplicated.json()["preset"]["id"]

        activated = self.client.post(f"/api/personalities/{preset_id}/activate")
        self.assertEqual(activated.status_code, 200)
        self.assertEqual(activated.json()["active_id"], preset_id)
        self.assertTrue(activated.json()["applied_live"])
        self.refresh_personality.assert_called()

        active_delete = self.client.delete(f"/api/personalities/{preset_id}")
        self.assertEqual(active_delete.status_code, 409)

        self.assertEqual(
            self.client.post("/api/personalities/gamer/activate").status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(f"/api/personalities/{preset_id}").status_code,
            204,
        )
        self.assertEqual(
            self.client.delete(f"/api/personalities/{duplicate_id}").status_code,
            204,
        )
        self.assertEqual(self.service_factory_calls, 0)

    def test_built_in_is_immutable_and_unknown_fields_are_rejected(self) -> None:
        edit = self.client.patch(
            "/api/personalities/gamer",
            json={"name": "Changed"},
        )
        delete = self.client.delete("/api/personalities/gamer")
        unknown = self.client.post(
            "/api/personalities",
            json={
                "name": "Test",
                "prompt": "This prompt is long enough for validation to pass safely.",
                "unknown": True,
            },
        )

        self.assertEqual(edit.status_code, 409)
        self.assertEqual(delete.status_code, 409)
        self.assertEqual(unknown.status_code, 422)


if __name__ == "__main__":
    unittest.main()
