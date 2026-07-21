from __future__ import annotations

import tempfile
import unittest

from pathlib import Path

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.avatar_models import AvatarModelStore
from app.history import ChatHistoryStore
from tests.test_avatar_models import make_vrm


class AvatarModelAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = AvatarModelStore(
            root / "avatar" / "model.vrm",
            root / "avatar" / "model.json",
            max_file_bytes=1024 * 1024,
        )
        self.service_factory_calls = 0

        def service_factory():
            self.service_factory_calls += 1
            raise AssertionError("Avatar model routes must remain lightweight")

        runtime = BackendRuntime(
            service_factory=service_factory,
            history_factory=lambda: ChatHistoryStore(root / "history.db"),
        )
        self.context = TestClient(
            create_app(runtime, avatar_model_store=self.store)
        )
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self.temporary_directory.cleanup()

    def test_upload_status_stream_and_delete_model(self) -> None:
        empty = self.client.get("/api/avatar/model")
        self.assertEqual(empty.status_code, 200)
        self.assertFalse(empty.json()["configured"])

        payload = make_vrm()
        uploaded = self.client.post(
            "/api/avatar/model",
            content=payload,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Akira-Filename": "My%20Avatar.vrm",
            },
        )
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(uploaded.json()["filename"], "My Avatar.vrm")
        self.assertEqual(uploaded.json()["vrm_version"], "1.0")

        streamed = self.client.get("/api/avatar/model/file")
        self.assertEqual(streamed.status_code, 200)
        self.assertEqual(streamed.content, payload)
        self.assertEqual(streamed.headers["cache-control"], "no-store")

        deleted = self.client.delete("/api/avatar/model")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(deleted.json()["configured"])
        self.assertEqual(
            self.client.get("/api/avatar/model/file").status_code,
            404,
        )
        self.assertEqual(self.service_factory_calls, 0)

    def test_invalid_model_returns_safe_validation_error(self) -> None:
        response = self.client.post(
            "/api/avatar/model",
            content=b"not a vrm",
            headers={"X-Akira-Filename": "broken.vrm"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("incomplete", response.json()["detail"])
        self.assertFalse(self.store.status().configured)

    def test_announced_oversized_model_is_rejected_before_storage(self) -> None:
        response = self.client.post(
            "/api/avatar/model",
            content=b"x",
            headers={
                "X-Akira-Filename": "large.vrm",
                "Content-Length": str(self.store.max_file_bytes + 1),
            },
        )

        self.assertEqual(response.status_code, 413)
        self.assertFalse(self.store.status().configured)


if __name__ == "__main__":
    unittest.main()
