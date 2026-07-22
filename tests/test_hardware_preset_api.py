from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.hardware_presets import GGUFModelProfile, GPUInfo, HardwareProfile
from config.settings import get_settings, update_settings

GIB = 1024**3


class HardwarePresetApiTests(unittest.TestCase):
    def test_lists_all_gpus_and_applies_editable_default(self):
        profile = HardwareProfile(
            logical_cpu_count=16,
            total_memory_bytes=32 * GIB,
            gpus=(GPUInfo("Test GPU A", 12 * GIB), GPUInfo("Test GPU B", 8 * GIB)),
        )
        model = GGUFModelProfile(
            size_bytes=8 * GIB,
            layer_count=40,
            kv_bytes_per_token=256 * 1024,
        )

        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            preset_file = Path(directory) / "hardware_presets.json"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "AKIRA_SETTINGS_FILE": str(settings_file),
                        "AKIRA_HARDWARE_PRESETS_FILE": str(preset_file),
                    },
                ),
                mock.patch("app.api.detect_hardware", return_value=profile),
                mock.patch("app.api.inspect_gguf_model", return_value=model),
                mock.patch(
                    "app.llm_runtime.retire_llm_runtime_contexts",
                    return_value=SimpleNamespace(
                        context_reset=True,
                        managed_processes_stopped=1,
                    ),
                ) as retire,
                mock.patch(
                    "ai.llm.get_default_llm",
                    return_value=SimpleNamespace(
                        process=SimpleNamespace(
                            config=SimpleNamespace(
                                context_size=12288,
                                gpu_layers="24",
                                threads=6,
                            ),
                            running=True,
                            pid=4321,
                        )
                    ),
                ) as get_default_llm,
                TestClient(create_app(BackendRuntime())) as client,
            ):
                update_settings({"llm": {"backend": "llama_cpp"}})

                listed = client.get("/api/models/hardware-presets")
                applied = client.post(
                    "/api/models/hardware-presets/apply",
                    json={
                        "preset_id": "high",
                        "context_size": 12288,
                        "gpu_layers": "24",
                        "threads": 6,
                        "set_default": True,
                    },
                )
                refreshed = client.get("/api/models/hardware-presets")

                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()["active_backend"], "llama_cpp")
                self.assertEqual(listed.json()["profile"]["logical_cpu_count"], 16)
                self.assertEqual(listed.json()["profile"]["total_vram_bytes"], 20 * GIB)
                self.assertEqual(len(listed.json()["profile"]["gpus"]), 2)
                self.assertEqual(len(listed.json()["presets"]), 4)
                self.assertEqual(listed.json()["recommended_id"], "ultra")
                self.assertEqual(applied.status_code, 200)
                self.assertEqual(applied.json()["preset"]["id"], "high")
                self.assertEqual(applied.json()["preset"]["gpu_layers"], "24")
                self.assertEqual(applied.json()["active_backend"], "llama_cpp")
                self.assertTrue(applied.json()["applied_to_active_backend"])
                self.assertTrue(applied.json()["saved_as_default"])
                self.assertTrue(applied.json()["context_reset"])
                self.assertEqual(applied.json()["runtime_state"], "restarted")
                self.assertEqual(applied.json()["runtime_pid"], 4321)
                self.assertEqual(applied.json()["configured_context_size"], 12288)
                self.assertEqual(applied.json()["configured_gpu_layers"], "24")
                self.assertEqual(applied.json()["configured_threads"], 6)
                self.assertEqual(applied.json()["active_context_size"], 12288)
                self.assertEqual(applied.json()["active_gpu_layers"], "24")
                self.assertEqual(applied.json()["active_threads"], 6)
                self.assertIsNone(applied.json()["restart_error"])
                retire.assert_called_once_with()
                get_default_llm.assert_called_once_with(reload=True)

                settings = get_settings()
                self.assertEqual(settings.llm.llama_cpp_context_size, 12288)
                self.assertEqual(settings.llm.llama_cpp_gpu_layers, "24")
                self.assertEqual(settings.llm.llama_cpp_threads, 6)
                self.assertEqual(refreshed.json()["current_id"], "high")
                self.assertEqual(refreshed.json()["default_id"], "high")
                high = next(
                    item for item in refreshed.json()["presets"] if item["id"] == "high"
                )
                self.assertTrue(high["customized"])
                self.assertIsNotNone(high["estimated_vram_bytes"])
                self.assertIsNotNone(high["estimated_ram_bytes"])

    def test_external_backend_saves_default_without_claiming_runtime_apply(self):
        profile = HardwareProfile(
            logical_cpu_count=8,
            total_memory_bytes=16 * GIB,
            gpus=(GPUInfo("Test GPU", 8 * GIB),),
        )
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            preset_file = Path(directory) / "hardware_presets.json"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "AKIRA_SETTINGS_FILE": str(settings_file),
                        "AKIRA_HARDWARE_PRESETS_FILE": str(preset_file),
                    },
                ),
                mock.patch("app.api.detect_hardware", return_value=profile),
                mock.patch(
                    "app.llm_runtime.retire_llm_runtime_contexts"
                ) as retire,
                TestClient(create_app(BackendRuntime())) as client,
            ):
                update_settings({"llm": {"backend": "lm_studio"}})
                response = client.post(
                    "/api/models/hardware-presets/apply",
                    json={
                        "preset_id": "medium",
                        "context_size": 6144,
                        "gpu_layers": "16",
                        "threads": 5,
                        "set_default": True,
                    },
                )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["active_backend"], "lm_studio")
                self.assertFalse(payload["applied_to_active_backend"])
                self.assertEqual(payload["runtime_state"], "inactive_backend")
                self.assertFalse(payload["context_reset"])
                self.assertTrue(payload["saved_as_default"])
                retire.assert_not_called()

                settings = get_settings()
                self.assertEqual(settings.llm.backend, "lm_studio")
                self.assertEqual(settings.llm.llama_cpp_context_size, 6144)
                self.assertEqual(settings.llm.llama_cpp_gpu_layers, "16")
                self.assertEqual(settings.llm.llama_cpp_threads, 5)

    def test_managed_backend_uses_shared_model_configuration_routes(self):
        profile = HardwareProfile(logical_cpu_count=8, total_memory_bytes=16 * GIB)
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            preset_file = Path(directory) / "hardware_presets.json"
            model_path = Path(directory) / "test-model.gguf"
            model_path.write_bytes(b"GGUF" + b"\0" * 32)
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "AKIRA_SETTINGS_FILE": str(settings_file),
                        "AKIRA_HARDWARE_PRESETS_FILE": str(preset_file),
                    },
                ),
                mock.patch("app.api.detect_hardware", return_value=profile),
                mock.patch(
                    "app.llm_runtime.retire_llm_runtime_contexts",
                    return_value=SimpleNamespace(
                        context_reset=True,
                        managed_processes_stopped=0,
                    ),
                ) as retire,
                TestClient(create_app(BackendRuntime())) as client,
            ):
                update_settings(
                    {
                        "llm": {
                            "backend": "llama_cpp",
                            "llama_cpp_model_path": str(model_path),
                            "llama_cpp_model_alias": "test-managed",
                            "llama_cpp_host": "127.0.0.1",
                            "llama_cpp_port": 8088,
                            "llama_cpp_context_size": 8192,
                        }
                    }
                )

                configured = client.get("/api/models/config")
                discovered = client.post(
                    "/api/models/discover",
                    json={
                        "backend": "llama_cpp",
                        "base_url": "http://127.0.0.1:8088/v1",
                        "api_key": "",
                    },
                )
                selected = client.post(
                    "/api/models/select",
                    json={
                        "backend": "llama_cpp",
                        "base_url": "http://127.0.0.1:8088/v1",
                        "api_key": "",
                        "model": "test-managed",
                        "reasoning_mode": "auto",
                    },
                )

                self.assertEqual(configured.status_code, 200)
                self.assertEqual(configured.json()["backend"], "llama_cpp")
                self.assertEqual(configured.json()["model"], "test-managed")
                self.assertEqual(
                    configured.json()["base_url"], "http://127.0.0.1:8088/v1"
                )
                self.assertEqual(
                    configured.json()["backend_urls"]["llama_cpp"],
                    "http://127.0.0.1:8088/v1",
                )
                self.assertEqual(discovered.status_code, 200)
                self.assertEqual(discovered.json()["backend"], "llama_cpp")
                self.assertEqual(len(discovered.json()["models"]), 1)
                self.assertEqual(discovered.json()["models"][0]["id"], "test-managed")
                self.assertEqual(
                    discovered.json()["models"][0]["reasoning_options"],
                    ["off", "on"],
                )
                self.assertEqual(
                    discovered.json()["models"][0]["default_reasoning"], "off"
                )
                self.assertEqual(selected.status_code, 200)
                self.assertEqual(selected.json()["backend"], "llama_cpp")
                self.assertEqual(selected.json()["model"], "test-managed")
                self.assertEqual(selected.json()["reasoning_mode"], "off")
                self.assertEqual(get_settings().llm.reasoning_mode, "off")
                retire.assert_called_once_with()

    def test_external_backend_urls_are_remembered_independently(self):
        profile = HardwareProfile(logical_cpu_count=8, total_memory_bytes=16 * GIB)
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            preset_file = Path(directory) / "hardware_presets.json"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "AKIRA_SETTINGS_FILE": str(settings_file),
                        "AKIRA_HARDWARE_PRESETS_FILE": str(preset_file),
                    },
                ),
                mock.patch("app.api.detect_hardware", return_value=profile),
                mock.patch(
                    "app.llm_runtime.retire_llm_runtime_contexts",
                    return_value=SimpleNamespace(
                        context_reset=False,
                        managed_processes_stopped=0,
                    ),
                ),
                TestClient(create_app(BackendRuntime())) as client,
            ):
                lm_url = "http://127.0.0.1:12340/v1"
                generic_url = "http://192.168.1.40:11434/v1"
                lm_selected = client.post(
                    "/api/models/select",
                    json={
                        "backend": "lm_studio",
                        "base_url": lm_url,
                        "api_key": "",
                        "model": "lm-model",
                        "reasoning_mode": "off",
                    },
                )
                generic_selected = client.post(
                    "/api/models/select",
                    json={
                        "backend": "openai_compatible",
                        "base_url": generic_url,
                        "api_key": "",
                        "model": "generic-model",
                        "reasoning_mode": "auto",
                    },
                )
                configured = client.get("/api/models/config")

                self.assertEqual(lm_selected.status_code, 200)
                self.assertEqual(generic_selected.status_code, 200)
                self.assertEqual(configured.status_code, 200)
                self.assertEqual(configured.json()["backend"], "openai_compatible")
                self.assertEqual(configured.json()["base_url"], generic_url)
                self.assertEqual(
                    configured.json()["backend_urls"],
                    {
                        "lm_studio": lm_url,
                        "openai_compatible": generic_url,
                        "llama_cpp": "http://127.0.0.1:8080/v1",
                    },
                )
                settings = get_settings()
                self.assertEqual(settings.llm.lm_studio_base_url, lm_url)
                self.assertEqual(
                    settings.llm.openai_compatible_base_url, generic_url
                )

    def test_reset_restores_builtin_values(self):
        profile = HardwareProfile(
            logical_cpu_count=16,
            total_memory_bytes=32 * GIB,
            gpus=(GPUInfo("Test GPU", 12 * GIB),),
        )
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            preset_file = Path(directory) / "hardware_presets.json"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "AKIRA_SETTINGS_FILE": str(settings_file),
                        "AKIRA_HARDWARE_PRESETS_FILE": str(preset_file),
                    },
                ),
                mock.patch("app.api.detect_hardware", return_value=profile),
                TestClient(create_app(BackendRuntime())) as client,
            ):
                client.post(
                    "/api/models/hardware-presets/apply",
                    json={
                        "preset_id": "medium",
                        "context_size": 6144,
                        "gpu_layers": "12",
                        "threads": 5,
                        "set_default": False,
                    },
                )
                response = client.post(
                    "/api/models/hardware-presets/reset",
                    json={"preset_id": "medium"},
                )

        self.assertEqual(response.status_code, 200)
        medium = next(item for item in response.json()["presets"] if item["id"] == "medium")
        self.assertEqual(medium["context_size"], 4096)
        self.assertEqual(medium["gpu_layers"], "auto")
        self.assertFalse(medium["customized"])

    def test_applying_unknown_preset_returns_404(self):
        profile = HardwareProfile(logical_cpu_count=8, total_memory_bytes=16 * GIB)
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            preset_file = Path(directory) / "hardware_presets.json"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "AKIRA_SETTINGS_FILE": str(settings_file),
                        "AKIRA_HARDWARE_PRESETS_FILE": str(preset_file),
                    },
                ),
                mock.patch("app.api.detect_hardware", return_value=profile),
                TestClient(create_app(BackendRuntime())) as client,
            ):
                response = client.post(
                    "/api/models/hardware-presets/apply",
                    json={
                        "preset_id": "unknown",
                        "context_size": 4096,
                        "gpu_layers": "0",
                        "threads": 4,
                    },
                )

        self.assertEqual(response.status_code, 404)
        self.assertIn("Unknown hardware preset", response.json()["detail"])

    def test_models_page_injects_hardware_preset_assets(self):
        with TestClient(create_app(BackendRuntime())) as client:
            response = client.get("/models")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/static/hardware-presets/styles.css", response.text)
        self.assertIn("/static/hardware-presets/app.js", response.text)


if __name__ == "__main__":
    unittest.main()
