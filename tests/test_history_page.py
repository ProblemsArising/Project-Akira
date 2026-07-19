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


class HistoryPageTests(unittest.TestCase):
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
            raise AssertionError("History browsing must stay lightweight")

        self.history = ChatHistoryStore(self.history_file)
        self.runtime = BackendRuntime(
            service_factory=service_factory,
            history_factory=lambda: self.history,
        )
        self.client_context = TestClient(create_app(self.runtime))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.environment.stop()
        self.temp_directory.cleanup()

    def _record(self, user: str, assistant: str) -> int:
        conversation_id, _ = self.history.record_turn(
            conversation_id=None,
            user_text=user,
            assistant_text=assistant,
            source="text",
            spoken=False,
        )
        return conversation_id

    def test_history_page_and_assets_do_not_load_service(self) -> None:
        page = self.client.get("/history")
        stylesheet = self.client.get("/static/history/styles.css")
        script = self.client.get("/static/history/app.js")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Conversation history", page.text)
        self.assertIn("/static/history/app.js", page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(".conversation-card", stylesheet.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("/api/conversations", script.text)
        self.assertIn("Continue chat", page.text)
        self.assertIn("/activate", script.text)
        self.assertEqual(self.service_factory_calls, 0)

    def test_search_summary_rename_and_delete_endpoints(self) -> None:
        matching_id = self._record("Talk about Minecraft", "Let's gather birch wood.")
        other_id = self._record("Hardware update", "The GPU is working.")

        searched = self.client.get("/api/conversations", params={"query": "birch"})
        self.assertEqual(searched.status_code, 200)
        self.assertEqual([item["id"] for item in searched.json()], [matching_id])

        summary = self.client.get(f"/api/conversations/{matching_id}/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["turn_count"], 1)

        renamed = self.client.patch(
            f"/api/conversations/{matching_id}",
            json={"title": "Minecraft planning"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["title"], "Minecraft planning")

        deleted = self.client.delete(f"/api/conversations/{matching_id}")
        self.assertEqual(deleted.status_code, 204)
        self.assertIsNone(self.history.get_conversation(matching_id))
        self.assertIsNotNone(self.history.get_conversation(other_id))
        self.assertEqual(self.service_factory_calls, 0)

    def test_missing_conversation_mutations_return_404(self) -> None:
        self.assertEqual(
            self.client.get("/api/conversations/999/summary").status_code,
            404,
        )
        self.assertEqual(
            self.client.patch(
                "/api/conversations/999",
                json={"title": "Missing"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete("/api/conversations/999").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
