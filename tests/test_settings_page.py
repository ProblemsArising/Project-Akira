from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.history import ChatHistoryStore
from config.settings import get_settings


class SettingsPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        root = Path(self.temp_directory.name)
        self.settings_file = root / "settings.json"
        self.history_file = root / "history.db"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "AKIRA_SETTINGS_FILE": str(self.settings_file),
                "AKIRA_HISTORY_FILE": str(self.history_file),
            },
        )
        self.environment.start()
        get_settings(reload=True)
        self.service_factory_calls = 0

        def service_factory():
            self.service_factory_calls += 1
            raise AssertionError("Settings pages and writes must stay lightweight")

        self.runtime = BackendRuntime(
            service_factory=service_factory,
            history_factory=lambda: ChatHistoryStore(self.history_file),
        )
        self.client_context = TestClient(create_app(self.runtime))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_settings_page_and_assets_do_not_load_service(self) -> None:
        page = self.client.get("/settings")
        stylesheet = self.client.get("/static/settings/styles.css")
        script = self.client.get("/static/settings/app.js")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Customize Akira", page.text)
        self.assertIn("/static/settings/app.js", page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(".settings-card", stylesheet.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn('"/api/settings"', script.text)
        self.assertEqual(self.service_factory_calls, 0)

    def test_partial_update_persists_and_returns_changed_sections(self) -> None:
        response = self.client.patch(
            "/api/settings",
            json={
                "changes": {
                    "llm": {"max_tokens": 1536, "temperature": 0.6},
                    "stt": {"device": "cpu", "language": "en"},
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["changed_sections"], ["llm", "stt"])
        self.assertFalse(payload["restart_required"])
        self.assertEqual(payload["settings"]["llm"]["max_tokens"], 1536)
        self.assertEqual(get_settings(reload=True).stt.device, "cpu")
        self.assertEqual(self.service_factory_calls, 0)

    def test_invalid_updates_are_rejected_without_changing_file(self) -> None:
        original = get_settings().llm.max_tokens

        unknown = self.client.patch(
            "/api/settings",
            json={"changes": {"llm": {"not_a_setting": 10}}},
        )
        wrong_type = self.client.patch(
            "/api/settings",
            json={"changes": {"llm": {"max_tokens": "many"}}},
        )
        unsafe_range = self.client.patch(
            "/api/settings",
            json={"changes": {"tts": {"volume": 4.0}}},
        )

        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(wrong_type.status_code, 422)
        self.assertEqual(unsafe_range.status_code, 422)
        self.assertEqual(get_settings(reload=True).llm.max_tokens, original)

    def test_reset_restores_defaults(self) -> None:
        self.client.patch(
            "/api/settings",
            json={"changes": {"llm": {"max_tokens": 1536}}},
        )
        response = self.client.post("/api/settings/reset")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"]["llm"]["max_tokens"], 1024)
        self.assertIn("avatar", response.json()["changed_sections"])


if __name__ == "__main__":
    unittest.main()
