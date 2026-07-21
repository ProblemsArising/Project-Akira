from __future__ import annotations

import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace

from app.discord_access import DiscordAccessPolicy
from app.discord_settings import (
    DiscordSettings,
    DiscordSettingsController,
    DiscordSettingsStore,
)


class FakeTokenStore:
    def __init__(self, token=None):
        self.token = token
        self.saved = []
        self.deleted = 0

    def save_token(self, token):
        self.token = token
        self.saved.append(token)

    def load_token(self):
        return self.token

    def delete_token(self):
        existed = self.token is not None
        self.token = None
        self.deleted += 1
        return existed

    def status(self):
        return SimpleNamespace(configured=self.token is not None)


class FakeAdapter:
    def __init__(self):
        self.is_running = False
        self.started_tokens = []
        self.stop_calls = 0

    def start(self, token, *, timeout=5.0):
        self.started_tokens.append(token)
        self.is_running = True
        return True

    def stop(self, *, timeout=5.0):
        self.stop_calls += 1
        changed = self.is_running
        self.is_running = False
        return changed

    def snapshot(self):
        return SimpleNamespace(
            state=SimpleNamespace(value="running" if self.is_running else "stopped"),
            health=SimpleNamespace(value="healthy" if self.is_running else "stopped"),
            running=self.is_running,
            connected=self.is_running,
            ready=self.is_running,
            reconnect_enabled=True,
            reconnect_count=0,
            disconnect_count=0,
            latency_ms=25.0 if self.is_running else None,
            uptime_seconds=5.0 if self.is_running else None,
        )


class DiscordSettingsStoreTests(unittest.TestCase):
    def test_settings_persist_ids_as_strings_without_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discord_settings.json"
            store = DiscordSettingsStore(path)

            saved = store.save(
                DiscordSettings(
                    enabled=True,
                    allowed_user_ids=(987654321012345678, 123),
                )
            )
            loaded = DiscordSettingsStore(path).load()
            text = path.read_text(encoding="utf-8")

            self.assertEqual(saved, loaded)
            self.assertIn('"987654321012345678"', text)
            self.assertNotIn("bot_token", text)
            self.assertNotIn("secret", text)

    def test_invalid_file_is_backed_up_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discord_settings.json"
            path.write_text("{broken", encoding="utf-8")

            settings = DiscordSettingsStore(path).load()

            self.assertEqual(settings, DiscordSettings())
            self.assertFalse(path.exists())
            self.assertEqual(len(list(path.parent.glob("discord_settings.broken-*.json"))), 1)


class DiscordSettingsControllerTests(unittest.TestCase):
    def make_controller(self, directory, *, token=None):
        token_store = FakeTokenStore(token)
        adapter = FakeAdapter()
        policy = DiscordAccessPolicy()
        controller = DiscordSettingsController(
            settings_store=DiscordSettingsStore(
                Path(directory) / "discord_settings.json"
            ),
            token_store=token_store,
            access_policy=policy,
            adapter=adapter,
        )
        return controller, token_store, policy, adapter

    def test_configure_saves_token_applies_policy_and_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, token_store, policy, adapter = self.make_controller(directory)

            snapshot = controller.configure(
                enabled=True,
                allowed_user_ids=["123", "987654321012345678"],
                bot_token="  secret-token  ",
            )

            self.assertEqual(token_store.saved, ["secret-token"])
            self.assertEqual(adapter.started_tokens, ["secret-token"])
            self.assertEqual(policy.allowed_user_ids, (123, 987654321012345678))
            self.assertTrue(snapshot.enabled)
            self.assertTrue(snapshot.token_configured)
            self.assertEqual(
                snapshot.allowed_user_ids,
                ("123", "987654321012345678"),
            )
            settings_text = (Path(directory) / "discord_settings.json").read_text()
            self.assertNotIn("secret-token", settings_text)

    def test_enable_requires_token_and_allowed_user(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, _, _, _ = self.make_controller(directory)

            with self.assertRaisesRegex(ValueError, "allowed Discord user ID"):
                controller.configure(enabled=True, allowed_user_ids=[])

            with self.assertRaisesRegex(ValueError, "bot token"):
                controller.configure(enabled=True, allowed_user_ids=[123])

    def test_stop_persists_disabled_and_delete_token_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, token_store, _, adapter = self.make_controller(
                directory, token="saved-token"
            )
            controller.configure(enabled=True, allowed_user_ids=[123])

            stopped = controller.stop(persist=True)
            self.assertFalse(stopped.enabled)
            self.assertFalse(adapter.is_running)

            removed = controller.delete_token()
            self.assertFalse(removed.token_configured)
            self.assertEqual(token_store.deleted, 1)

    def test_start_if_enabled_never_breaks_application_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, _, _, adapter = self.make_controller(directory)
            controller._settings_store.save(
                DiscordSettings(enabled=True, allowed_user_ids=(123,))
            )

            self.assertFalse(controller.start_if_enabled())
            self.assertFalse(adapter.is_running)


if __name__ == "__main__":
    unittest.main()
