from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.installs: list[str] = []
        self.cancelled = False
        self.removed = False
        self.shutdown_called = False
        self.data = {
            "supported": True,
            "version": "b10080",
            "root": "C:/Akira/Data/runtimes/llama.cpp",
            "recommended_variant": "cuda12",
            "variants": [
                {
                    "id": "cuda12",
                    "name": "NVIDIA CUDA 12",
                    "description": "NVIDIA runtime",
                    "asset_count": 2,
                    "recommended": True,
                }
            ],
            "installed": False,
            "installed_version": None,
            "installed_variant": None,
            "executable": None,
            "devices": [],
            "job": None,
        }

    def snapshot(self):
        return copy.deepcopy(self.data)

    def start_install(self, variant):
        self.installs.append(variant)
        job = {
            "id": "job-1",
            "version": "b10080",
            "variant": variant,
            "status": "downloading",
            "current_asset": "runtime.zip",
            "downloaded_bytes": 10,
            "total_bytes": 100,
            "error": None,
            "executable": None,
            "devices": None,
            "progress": 0.1,
        }
        self.data["job"] = job
        return SimpleNamespace(id="job-1", variant=variant, version="b10080")

    def cancel(self):
        self.cancelled = True
        self.data["job"]["status"] = "cancelled"
        return SimpleNamespace(id="job-1", variant="cuda12")

    def remove(self):
        self.removed = True
        self.data.update(
            {
                "installed": False,
                "installed_version": None,
                "installed_variant": None,
                "executable": None,
                "devices": [],
                "job": None,
            }
        )
        return self.snapshot()

    def shutdown(self):
        self.shutdown_called = True


class LlamaCppRuntimeApiTests(unittest.TestCase):
    def test_status_and_install_use_runtime_manager(self):
        manager = FakeRuntimeManager()
        runtime = BackendRuntime(llama_cpp_runtime_factory=lambda: manager)
        settings = SimpleNamespace(llm=SimpleNamespace(backend="lm_studio"))
        with (
            mock.patch("app.api.get_settings", return_value=settings),
            TestClient(create_app(runtime)) as client,
        ):
            status = client.get("/api/llama-cpp/runtime")
            started = client.post(
                "/api/llama-cpp/runtime/install",
                json={"variant": "cuda12"},
            )

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["recommended_variant"], "cuda12")
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json()["job"]["status"], "downloading")
        self.assertEqual(manager.installs, ["cuda12"])
        self.assertTrue(manager.shutdown_called)

    def test_cancel_and_remove_are_exposed(self):
        manager = FakeRuntimeManager()
        manager.start_install("cuda12")
        manager.data.update(
            {
                "installed": True,
                "installed_version": "b10080",
                "installed_variant": "cuda12",
                "executable": "C:/Akira/llama-server.exe",
            }
        )
        runtime = BackendRuntime(llama_cpp_runtime_factory=lambda: manager)
        settings = SimpleNamespace(llm=SimpleNamespace(backend="lm_studio"))
        with (
            mock.patch("app.api.get_settings", return_value=settings),
            TestClient(create_app(runtime)) as client,
        ):
            cancelled = client.post("/api/llama-cpp/runtime/cancel")
            removed = client.delete("/api/llama-cpp/runtime")

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["job"]["status"], "cancelled")
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(removed.json()["installed"])
        self.assertTrue(manager.cancelled)
        self.assertTrue(manager.removed)


if __name__ == "__main__":
    unittest.main()
