from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from app.backend_health import BackendHealthChecker


class FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        status_code: int = 200,
        text: str = "",
        json_error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.json_error = json_error

    def json(self) -> object:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeClient:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("FakeClient needs a response or error")
        return self.response


class FakeClock:
    def __init__(self, elapsed_seconds: float = 0.012) -> None:
        self.value = 100.0
        self.elapsed_seconds = elapsed_seconds
        self.calls = 0

    def __call__(self) -> float:
        value = self.value
        self.calls += 1
        if self.calls % 2:
            self.value += self.elapsed_seconds
        return value


def settings_for(
    backend: str,
    *,
    base_url: str = "http://127.0.0.1:1234/v1",
    model: str = "test-model",
) -> SimpleNamespace:
    return SimpleNamespace(
        llm=SimpleNamespace(
            backend=backend,
            base_url=base_url,
            api_key="None",
            model=model,
        )
    )


def managed_config() -> SimpleNamespace:
    return SimpleNamespace(
        executable="C:/llama/llama-server.exe",
        model_path="C:/models/test.gguf",
        model_alias="test-managed",
        base_url="http://127.0.0.1:8080/v1",
        health_url="http://127.0.0.1:8080/health",
        log_file="C:/Akira/data/logs/llama-server.log",
        context_size=8192,
        gpu_layers="all",
        threads=8,
        parallel_slots=1,
    )


class BackendHealthCheckerTests(unittest.TestCase):
    def checker(
        self,
        client: FakeClient,
        *,
        snapshots: tuple[object, ...] = (),
    ) -> BackendHealthChecker:
        return BackendHealthChecker(
            http_client=client,
            managed_snapshot=lambda: snapshots,
            clock=FakeClock(),
            now=lambda: datetime(2026, 7, 22, 20, 15, tzinfo=timezone.utc),
        )

    def test_lm_studio_requires_selected_model_to_be_loaded(self):
        client = FakeClient(
            FakeResponse(
                {
                    "models": [
                        {
                            "key": "test-model",
                            "loaded_instances": [{"id": "instance-1"}],
                        },
                        {"key": "other-model", "loaded_instances": []},
                    ]
                }
            )
        )

        result = self.checker(client).check(
            settings=settings_for("lm_studio")
        )

        self.assertEqual(result.status, "healthy")
        self.assertTrue(result.reachable)
        self.assertTrue(result.model_available)
        self.assertTrue(result.model_loaded)
        self.assertEqual(result.latency_ms, 12.0)
        self.assertEqual(
            client.requests[0]["url"],
            "http://127.0.0.1:1234/api/v1/models",
        )

    def test_lm_studio_reports_available_but_unloaded_model(self):
        client = FakeClient(
            FakeResponse(
                {
                    "models": [
                        {"key": "test-model", "loaded_instances": []},
                    ]
                }
            )
        )

        result = self.checker(client).check(
            settings=settings_for("lm_studio")
        )

        self.assertEqual(result.status, "degraded")
        self.assertTrue(result.model_available)
        self.assertFalse(result.model_loaded)
        self.assertIn("not loaded", result.message)

    def test_openai_compatible_requires_model_in_v1_models(self):
        client = FakeClient(
            FakeResponse({"data": [{"id": "test-model"}, {"id": "other"}]})
        )

        result = self.checker(client).check(
            settings=settings_for(
                "openai_compatible",
                base_url="http://127.0.0.1:11434/v1",
            )
        )

        self.assertEqual(result.status, "healthy")
        self.assertTrue(result.model_available)
        self.assertIsNone(result.model_loaded)
        self.assertEqual(
            client.requests[0]["url"],
            "http://127.0.0.1:11434/v1/models",
        )

    def test_unreachable_external_backend_is_offline(self):
        client = FakeClient(error=OSError("connection refused"))

        result = self.checker(client).check(
            settings=settings_for("openai_compatible")
        )

        self.assertEqual(result.status, "offline")
        self.assertFalse(result.reachable)
        self.assertIn("connection refused", result.message)

    def test_empty_edited_url_is_misconfigured_not_replaced_by_saved_url(self):
        client = FakeClient(error=AssertionError("HTTP should not be called"))

        result = self.checker(client).check(
            settings=settings_for("lm_studio"),
            base_url="",
        )

        self.assertEqual(result.status, "misconfigured")
        self.assertEqual(client.requests, [])

    def test_managed_llama_cpp_idle_does_not_start_a_process(self):
        module = types.ModuleType("ai.llama_cpp_backend")
        module.llama_cpp_process_config = lambda settings: managed_config()
        module.managed_llama_cpp_snapshot = lambda: ()
        client = FakeClient(error=OSError("connection refused"))

        with mock.patch.dict(sys.modules, {"ai.llama_cpp_backend": module}):
            result = self.checker(client).check(
                settings=settings_for("llama_cpp")
            )

        self.assertEqual(result.status, "idle")
        self.assertTrue(result.managed)
        self.assertFalse(result.process_running)
        self.assertFalse(result.model_loaded)
        self.assertIn("start it automatically", result.message)
        self.assertEqual(result.details["parallel_slots"], 1)

    def test_managed_llama_cpp_reports_running_process_and_pid(self):
        module = types.ModuleType("ai.llama_cpp_backend")
        module.llama_cpp_process_config = lambda settings: managed_config()
        snapshot = SimpleNamespace(
            base_url="http://127.0.0.1:8080/v1",
            running=True,
            pid=4321,
            exit_code=None,
        )
        client = FakeClient(FakeResponse({"status": "ok"}))

        with mock.patch.dict(sys.modules, {"ai.llama_cpp_backend": module}):
            result = self.checker(client, snapshots=(snapshot,)).check(
                settings=settings_for("llama_cpp")
            )

        self.assertEqual(result.status, "healthy")
        self.assertTrue(result.process_running)
        self.assertEqual(result.process_pid, 4321)
        self.assertTrue(result.model_loaded)

    def test_running_managed_process_that_is_not_ready_is_degraded(self):
        module = types.ModuleType("ai.llama_cpp_backend")
        module.llama_cpp_process_config = lambda settings: managed_config()
        snapshot = SimpleNamespace(
            base_url="http://127.0.0.1:8080/v1",
            running=True,
            pid=4321,
            exit_code=None,
        )
        client = FakeClient(
            FakeResponse(
                {"error": "loading"},
                status_code=503,
                text="loading model",
            )
        )

        with mock.patch.dict(sys.modules, {"ai.llama_cpp_backend": module}):
            result = self.checker(client, snapshots=(snapshot,)).check(
                settings=settings_for("llama_cpp")
            )

        self.assertEqual(result.status, "degraded")
        self.assertTrue(result.reachable)
        self.assertTrue(result.process_running)
        self.assertEqual(result.process_pid, 4321)
        self.assertIn("not ready", result.message)

    def test_server_on_managed_port_without_registry_owner_is_degraded(self):
        module = types.ModuleType("ai.llama_cpp_backend")
        module.llama_cpp_process_config = lambda settings: managed_config()
        client = FakeClient(FakeResponse({"status": "ok"}))

        with mock.patch.dict(sys.modules, {"ai.llama_cpp_backend": module}):
            result = self.checker(client).check(
                settings=settings_for("llama_cpp")
            )

        self.assertEqual(result.status, "degraded")
        self.assertTrue(result.reachable)
        self.assertFalse(result.process_running)
        self.assertTrue(result.details["external_server"])
        self.assertIn("did not launch it", result.message)

    def test_invalid_managed_configuration_is_reported_without_http(self):
        module = types.ModuleType("ai.llama_cpp_backend")

        def invalid_config(settings: object) -> object:
            raise ValueError("No GGUF model is configured")

        module.llama_cpp_process_config = invalid_config
        client = FakeClient(error=AssertionError("HTTP should not be called"))

        with mock.patch.dict(sys.modules, {"ai.llama_cpp_backend": module}):
            result = self.checker(client).check(
                settings=settings_for("llama_cpp")
            )

        self.assertEqual(result.status, "misconfigured")
        self.assertEqual(client.requests, [])
        self.assertIn("No GGUF", result.message)


if __name__ == "__main__":
    unittest.main()
