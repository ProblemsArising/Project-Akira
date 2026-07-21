from __future__ import annotations

import tempfile
import unittest

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.discord_access import DiscordAccessPolicy
from app.discord_api import register_discord_routes
from app.discord_settings import DiscordSettingsController, DiscordSettingsStore
from tests.test_discord_settings import FakeAdapter, FakeTokenStore


class DiscordSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        web_root = root / "web"
        web_root.mkdir()
        (web_root / "index.html").write_text("Discord settings", encoding="utf-8")
        (web_root / "app.js").write_text("console.log('discord')", encoding="utf-8")

        self.token_store = FakeTokenStore()
        self.adapter = FakeAdapter()
        self.policy = DiscordAccessPolicy()
        self.controller = DiscordSettingsController(
            settings_store=DiscordSettingsStore(root / "discord_settings.json"),
            token_store=self.token_store,
            access_policy=self.policy,
            adapter=self.adapter,
        )

        app = FastAPI()
        app.state.discord_controller = self.controller
        register_discord_routes(app, web_root)
        self.client = TestClient(app)

    def tearDown(self):
        self.temporary.cleanup()

    def test_page_and_assets_are_served(self):
        page = self.client.get("/discord")
        asset = self.client.get("/static/discord/app.js")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Discord settings", page.text)
        self.assertEqual(asset.status_code, 200)

    def test_settings_api_never_returns_token(self):
        response = self.client.put(
            "/api/discord/settings",
            json={
                "enabled": True,
                "allowed_user_ids": ["123456789012345678"],
                "bot_token": "secret-token",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["token_configured"])
        self.assertNotIn("secret-token", response.text)
        self.assertNotIn("bot_token", body)
        self.assertEqual(body["allowed_user_ids"], ["123456789012345678"])

    def test_start_validates_configuration(self):
        response = self.client.post("/api/discord/start")

        self.assertEqual(response.status_code, 422)
        self.assertIn("allowed Discord user ID", response.json()["detail"])

    def test_stop_and_remove_token(self):
        self.client.put(
            "/api/discord/settings",
            json={
                "enabled": True,
                "allowed_user_ids": ["123"],
                "bot_token": "secret-token",
            },
        )

        stopped = self.client.post("/api/discord/stop")
        removed = self.client.delete("/api/discord/token")

        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(stopped.json()["enabled"])
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(removed.json()["token_configured"])


if __name__ == "__main__":
    unittest.main()
