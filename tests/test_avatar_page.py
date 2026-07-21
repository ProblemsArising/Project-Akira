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


class AvatarPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        root = Path(self.temp_directory.name)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "AKIRA_SETTINGS_FILE": str(root / "settings.json"),
                "AKIRA_HISTORY_FILE": str(root / "history.db"),
            },
        )
        self.environment.start()
        get_settings(reload=True)
        self.service_factory_calls = 0

        def service_factory():
            self.service_factory_calls += 1
            raise AssertionError("Opening the avatar page must remain lightweight")

        runtime = BackendRuntime(
            service_factory=service_factory,
            history_factory=lambda: ChatHistoryStore(root / "history.db"),
        )
        self.context = TestClient(create_app(runtime))
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_avatar_page_and_assets_do_not_load_conversation_service(self) -> None:
        page = self.client.get("/avatar")
        stylesheet = self.client.get("/static/avatar/styles.css")
        script = self.client.get("/static/avatar/app.js")
        renderer = self.client.get("/static/avatar/renderer.js")
        three = self.client.get(
            "/static/avatar/vendor/three/three.module.min.js"
        )
        three_vrm = self.client.get(
            "/static/avatar/vendor/three-vrm/three-vrm.module.min.js"
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn('id="avatarCore"', page.text)
        self.assertIn('id="rendererHost"', page.text)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn('body[data-state="speaking"]', stylesheet.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("/api/events", script.text)
        self.assertEqual(renderer.status_code, 200)
        self.assertIn("VRMLoaderPlugin", renderer.text)
        self.assertEqual(three.status_code, 200)
        self.assertEqual(three_vrm.status_code, 200)
        self.assertEqual(self.service_factory_calls, 0)


if __name__ == "__main__":
    unittest.main()
