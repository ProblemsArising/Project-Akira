from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.model_downloads import ModelDownloadJob


class FakeDownloadManager:
    def __init__(self, root: Path):
        self.root = root
        self.started = []
        self.cancelled = []
        self.deleted = []
        self.shutdown_called = False
        self.job = ModelDownloadJob(
            id="job-1",
            url="https://models.example/test.gguf",
            filename="test.gguf",
            destination=str(root / "test.gguf"),
            status="downloading",
            downloaded_bytes=5,
            total_bytes=10,
        )

    def snapshot(self, *, active_path=None):
        return {
            "directory": str(self.root),
            "jobs": [self.job.to_dict()],
            "models": [],
        }

    def start_download(self, **values):
        self.started.append(values)
        return self.job

    def cancel_download(self, job_id):
        self.cancelled.append(job_id)
        self.job.status = "cancelled"
        return self.job

    def resolve_model(self, filename):
        return (self.root / filename).resolve()

    def delete_model(self, filename, *, active_path=None):
        self.deleted.append((filename, active_path))

    def shutdown(self, timeout=2.0):
        self.shutdown_called = True


class ModelDownloadApiTests(unittest.TestCase):
    def test_download_management_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = FakeDownloadManager(root)
            runtime = BackendRuntime(model_download_factory=lambda: manager)
            settings_file = root / "settings.json"

            with mock.patch.dict(
                "os.environ",
                {"AKIRA_SETTINGS_FILE": str(settings_file)},
            ), mock.patch(
                "app.llm_runtime.retire_llm_runtime_contexts",
                return_value=SimpleNamespace(context_reset=True),
            ), TestClient(create_app(runtime)) as client:
                listed = client.get("/api/models/downloads")
                started = client.post(
                    "/api/models/downloads",
                    json={
                        "url": "https://models.example/test.gguf",
                        "filename": "test.gguf",
                        "sha256": "",
                    },
                )
                cancelled = client.post("/api/models/downloads/job-1/cancel")
                selected = client.post(
                    "/api/models/downloads/select",
                    json={"filename": "test.gguf"},
                )
                deleted = client.delete("/api/models/downloads/local/other.gguf")

            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["directory"], str(Path(directory)))
            self.assertEqual(started.status_code, 202)
            self.assertEqual(started.json()["id"], "job-1")
            self.assertEqual(manager.started[0]["filename"], "test.gguf")
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["status"], "cancelled")
            self.assertEqual(manager.cancelled, ["job-1"])
            self.assertEqual(selected.status_code, 200)
            self.assertEqual(selected.json()["backend"], "llama_cpp")
            self.assertEqual(selected.json()["model_alias"], "test")
            self.assertTrue(selected.json()["context_reset"])
            self.assertEqual(deleted.status_code, 204)
            self.assertEqual(manager.deleted[0][0], "other.gguf")
            self.assertTrue(manager.shutdown_called)

    def test_models_page_injects_download_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FakeDownloadManager(Path(directory))
            runtime = BackendRuntime(model_download_factory=lambda: manager)

            with TestClient(create_app(runtime)) as client:
                response = client.get("/models")

            self.assertEqual(response.status_code, 200)
            self.assertIn("/static/model-downloads/styles.css", response.text)
            self.assertIn("/static/model-downloads/app.js", response.text)


if __name__ == "__main__":
    unittest.main()
