from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.backend_health import BackendHealth


READY = BackendHealth(
    backend="lm_studio",
    display_name="LM Studio",
    status="healthy",
    base_url="http://127.0.0.1:1234/v1",
    endpoint="http://127.0.0.1:1234/api/v1/models",
    message="LM Studio is ready.",
    checked_at="2026-07-22T20:15:00Z",
    reachable=True,
    latency_ms=12.5,
    model="test-model",
    model_available=True,
    model_loaded=True,
)


class BackendHealthApiTests(unittest.TestCase):
    def test_post_checks_edited_values_without_saving(self):
        with (
            mock.patch("app.api.check_backend_health", return_value=READY) as check,
            TestClient(create_app(BackendRuntime())) as client,
        ):
            response = client.post(
                "/api/models/health",
                json={
                    "backend": "lm_studio",
                    "base_url": "http://127.0.0.1:1234/v1",
                    "api_key": "secret-test-key",
                    "model": "test-model",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertEqual(response.json()["latency_ms"], 12.5)
        kwargs = check.call_args.kwargs
        self.assertEqual(kwargs["backend"], "lm_studio")
        self.assertEqual(kwargs["base_url"], "http://127.0.0.1:1234/v1")
        self.assertEqual(kwargs["api_key"], "secret-test-key")
        self.assertEqual(kwargs["model"], "test-model")

    def test_get_checks_saved_configuration(self):
        with (
            mock.patch("app.api.check_backend_health", return_value=READY) as check,
            TestClient(create_app(BackendRuntime())) as client,
        ):
            response = client.get("/api/models/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "LM Studio")
        self.assertIn("settings", check.call_args.kwargs)
        self.assertEqual(set(check.call_args.kwargs), {"settings"})

    def test_models_page_injects_backend_health_assets(self):
        with TestClient(create_app(BackendRuntime())) as client:
            response = client.get("/models")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/static/backend-health/styles.css", response.text)
        self.assertIn("/static/backend-health/app.js", response.text)


if __name__ == "__main__":
    unittest.main()
