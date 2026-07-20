from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.history import ChatHistoryStore
from app.model_backends import ModelDiscovery, ModelInfo
from config.settings import get_settings


class FakeModelBackendClient:
    def __init__(self):
        self.closed = False
        self.discover_calls = []
        self.load_calls = []
        self.unload_calls = []

    def close(self):
        self.closed = True

    def discover(self, *, backend, base_url, api_key=None):
        self.discover_calls.append((backend, base_url, api_key))
        return ModelDiscovery(
            backend=backend,
            base_url=base_url,
            api_url="http://localhost:1234/api/v1/models",
            models=[
                ModelInfo(
                    id="test/model-12b",
                    display_name="Test Model 12B",
                    publisher="test",
                    architecture="gemma",
                    quantization="Q4_K_M",
                    params="12B",
                    size_bytes=7_000_000_000,
                    max_context_length=32768,
                    loaded=True,
                    instance_ids=["test/model-12b"],
                    reasoning_options=["off", "on"],
                    default_reasoning="off",
                )
            ],
        )

    def load_lm_studio_model(self, **kwargs):
        self.load_calls.append(kwargs)
        return {
            "status": "loaded",
            "instance_id": "test/model-12b",
            "load_time_seconds": 1.2,
        }

    def unload_lm_studio_model(self, **kwargs):
        self.unload_calls.append(kwargs)
        return {"instance_id": kwargs["instance_id"]}


class ModelsPageTests(unittest.TestCase):
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
        self.model_clients = []

        def service_factory():
            self.service_factory_calls += 1
            raise AssertionError("Model selection must not load heavy services")

        def model_factory():
            client = FakeModelBackendClient()
            self.model_clients.append(client)
            return client

        self.runtime = BackendRuntime(
            service_factory=service_factory,
            history_factory=lambda: ChatHistoryStore(self.history_file),
            model_backend_factory=model_factory,
        )
        self.client_context = TestClient(create_app(self.runtime))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_page_and_assets_do_not_load_conversation_service(self) -> None:
        page = self.client.get("/models")
        stylesheet = self.client.get("/static/models/styles.css")
        script = self.client.get("/static/models/app.js")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Choose Akira’s brain", page.text)
        self.assertIn('/api/models/discover', script.text)
        self.assertIn('id="activeBadge"', page.text)
        self.assertIn('elements.activeBadge.hidden = !isActive', script.text)
        self.assertIn('.badge[hidden]', stylesheet.text)
        self.assertIn('.model-list', stylesheet.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(self.service_factory_calls, 0)

    def test_config_discovery_and_selection(self) -> None:
        config = self.client.get("/api/models/config")
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["backend"], "lm_studio")

        discovered = self.client.post(
            "/api/models/discover",
            json={
                "backend": "lm_studio",
                "base_url": "http://localhost:1234/v1",
                "api_key": "",
            },
        )
        self.assertEqual(discovered.status_code, 200)
        self.assertEqual(discovered.json()["models"][0]["id"], "test/model-12b")
        self.assertTrue(discovered.json()["models"][0]["loaded"])
        self.assertTrue(self.model_clients[-1].closed)

        selected = self.client.post(
            "/api/models/select",
            json={
                "backend": "lm_studio",
                "base_url": "http://localhost:1234/v1",
                "api_key": "",
                "model": "test/model-12b",
                "reasoning_mode": "off",
            },
        )
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["model"], "test/model-12b")
        self.assertEqual(get_settings(reload=True).llm.model, "test/model-12b")
        self.assertEqual(self.service_factory_calls, 0)

    def test_openai_backend_forces_reasoning_auto(self) -> None:
        selected = self.client.post(
            "/api/models/select",
            json={
                "backend": "openai_compatible",
                "base_url": "http://localhost:11434/v1",
                "api_key": "",
                "model": "local-model",
                "reasoning_mode": "off",
            },
        )

        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["reasoning_mode"], "auto")
        settings = get_settings(reload=True)
        self.assertEqual(settings.llm.backend, "openai_compatible")
        self.assertEqual(settings.llm.reasoning_mode, "auto")

    def test_lm_studio_load_and_unload_actions(self) -> None:
        loaded = self.client.post(
            "/api/models/load",
            json={
                "backend": "lm_studio",
                "base_url": "http://localhost:1234/v1",
                "api_key": "",
                "model": "test/model-12b",
                "context_length": 16384,
            },
        )
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["status"], "loaded")
        self.assertEqual(self.model_clients[-1].load_calls[0]["context_length"], 16384)

        unloaded = self.client.post(
            "/api/models/unload",
            json={
                "backend": "lm_studio",
                "base_url": "http://localhost:1234/v1",
                "api_key": "",
                "instance_id": "test/model-12b",
            },
        )
        self.assertEqual(unloaded.status_code, 200)
        self.assertEqual(unloaded.json()["status"], "unloaded")
        self.assertEqual(
            self.model_clients[-1].unload_calls[0]["instance_id"],
            "test/model-12b",
        )

    def test_invalid_backend_is_rejected(self) -> None:
        response = self.client.post(
            "/api/models/select",
            json={
                "backend": "not-real",
                "base_url": "http://localhost:1234/v1",
                "api_key": "",
                "model": "test",
                "reasoning_mode": "off",
            },
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
