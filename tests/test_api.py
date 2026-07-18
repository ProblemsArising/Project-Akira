from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.conversation import ConversationResult
from app.history import ChatHistoryStore
from config.settings import get_settings


class FakeConversationService:
    def __init__(self) -> None:
        self.current_conversation_id: int | None = None
        self.is_running = False
        self.is_listening = False
        self.messages: list[tuple[str, bool]] = []
        self.stop_requested = False
        self.new_conversation_titles: list[str | None] = []

    def process_text(
        self,
        text: str,
        *,
        speak: bool = True,
        source: str = "text",
        audio_file: str | None = None,
    ) -> ConversationResult | None:
        self.messages.append((text, speak))
        if text == "empty":
            return None
        self.current_conversation_id = self.current_conversation_id or 12
        return ConversationResult(
            user_text=text,
            reply=f"reply:{text}",
            source="text",
            audio_file=audio_file,
            spoken=speak,
        )

    def start_new_conversation(self, title: str | None = None) -> int:
        self.new_conversation_titles.append(title)
        self.current_conversation_id = 99
        return 99

    def start_listening(self) -> bool:
        if self.is_running:
            return False
        self.is_running = True
        self.is_listening = True
        return True

    def stop_listening(
        self,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> bool:
        changed = self.is_listening
        self.is_listening = False
        self.is_running = False
        return changed

    def request_stop(self) -> None:
        self.stop_requested = True
        self.is_listening = False
        self.is_running = False


class ApiBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_directory.name)
        self.settings_file = temp_root / "settings.json"
        self.history_file = temp_root / "history.db"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "AKIRA_SETTINGS_FILE": str(self.settings_file),
                "AKIRA_HISTORY_FILE": str(self.history_file),
            },
        )
        self.environment.start()
        get_settings(reload=True)

        self.fake_service = FakeConversationService()
        self.service_factory_calls = 0

        def service_factory() -> FakeConversationService:
            self.service_factory_calls += 1
            return self.fake_service

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

    def test_health_and_status_do_not_load_heavy_service(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

        runtime_status = self.client.get("/api/status")
        self.assertEqual(runtime_status.status_code, 200)
        self.assertFalse(runtime_status.json()["service_loaded"])
        self.assertEqual(self.service_factory_calls, 0)

    def test_chat_uses_conversation_service(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={"message": "Hello", "speak": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "reply:Hello")
        self.assertFalse(response.json()["spoken"])
        self.assertEqual(response.json()["conversation_id"], 12)
        self.assertEqual(self.fake_service.messages, [("Hello", False)])
        self.assertEqual(self.service_factory_calls, 1)

    def test_chat_rejects_blank_and_empty_model_response(self) -> None:
        blank = self.client.post("/api/chat", json={"message": ""})
        self.assertEqual(blank.status_code, 422)

        empty = self.client.post("/api/chat", json={"message": "empty"})
        self.assertEqual(empty.status_code, 422)
        self.assertIn("usable reply", empty.json()["detail"])

    def test_new_conversation_and_listening_controls(self) -> None:
        conversation = self.client.post(
            "/api/conversations",
            json={"title": "API test"},
        )
        self.assertEqual(conversation.status_code, 201)
        self.assertEqual(conversation.json()["conversation_id"], 99)

        started = self.client.post("/api/listening/start")
        self.assertEqual(started.status_code, 200)
        self.assertTrue(started.json()["changed"])
        self.assertTrue(started.json()["is_listening"])

        duplicate = self.client.post("/api/listening/start")
        self.assertFalse(duplicate.json()["changed"])

        stopped = self.client.post("/api/listening/stop")
        self.assertTrue(stopped.json()["changed"])
        self.assertFalse(stopped.json()["is_listening"])

    def test_history_endpoints_use_sqlite_store(self) -> None:
        conversation_id, _ = self.history.record_turn(
            conversation_id=None,
            user_text="History question",
            assistant_text="History answer",
            source="text",
            spoken=False,
        )

        conversations = self.client.get("/api/conversations")
        self.assertEqual(conversations.status_code, 200)
        self.assertEqual(conversations.json()[0]["id"], conversation_id)
        self.assertEqual(conversations.json()[0]["turn_count"], 1)

        turns = self.client.get(f"/api/conversations/{conversation_id}")
        self.assertEqual(turns.status_code, 200)
        self.assertEqual(turns.json()[0]["user_text"], "History question")
        self.assertEqual(turns.json()[0]["assistant_text"], "History answer")

    def test_settings_endpoint_returns_current_snapshot(self) -> None:
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        payload = response.json()["settings"]
        self.assertIn("llm", payload)
        self.assertIn("audio", payload)
        self.assertIn("avatar", payload)

    def test_unknown_request_fields_are_rejected(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={"message": "Hello", "unknown": True},
        )
        self.assertEqual(response.status_code, 422)

    def test_lifespan_stops_service(self) -> None:
        self.client.get("/api/status")
        _ = self.runtime.service
        self.assertFalse(self.fake_service.stop_requested)

        self.client_context.__exit__(None, None, None)
        self.assertTrue(self.fake_service.stop_requested)

        # Avoid closing the same context twice in tearDown.
        self.client_context = _NoopContext()


class _NoopContext:
    def __exit__(self, *args: object) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
