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


class ChatPageTests(unittest.TestCase):
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
            raise AssertionError(
                "Opening the static chat page must not load heavy components"
            )

        self.history = ChatHistoryStore(self.history_file)
        self.runtime = BackendRuntime(
            service_factory=service_factory,
            history_factory=lambda: self.history,
        )
        self.app = create_app(self.runtime)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_root_and_chat_routes_serve_interface_without_loading_service(self) -> None:
        root = self.client.get("/")
        chat = self.client.get("/chat")

        self.assertEqual(root.status_code, 200)
        self.assertEqual(chat.status_code, 200)
        self.assertIn("Project Akira", root.text)
        self.assertIn('id="composer"', root.text)
        self.assertIn("/static/chat/app.js", root.text)
        self.assertIn('href="/settings"', root.text)
        self.assertEqual(root.headers["cache-control"], "no-store")
        self.assertEqual(self.service_factory_calls, 0)

    def test_static_assets_are_served(self) -> None:
        stylesheet = self.client.get("/static/chat/styles.css")
        script = self.client.get("/static/chat/app.js")

        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(".chat-panel", stylesheet.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn('"/api/chat"', script.text)
        self.assertIn("/api/events", script.text)
        self.assertIn("/api/listening/start", script.text)
        self.assertEqual(self.service_factory_calls, 0)


if __name__ == "__main__":
    unittest.main()
