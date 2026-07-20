from __future__ import annotations

import unittest

from app.model_backends import (
    ModelBackendClient,
    ModelBackendError,
    compatibility_base_url,
    native_lm_studio_root,
)


class FakeResponse:
    def __init__(self, payload, *, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class ModelBackendClientTests(unittest.TestCase):
    def test_lm_studio_discovery_normalizes_rich_model_metadata(self):
        http = FakeHttpClient(
            [
                FakeResponse(
                    {
                        "models": [
                            {
                                "type": "llm",
                                "publisher": "google",
                                "key": "google/gemma-test",
                                "display_name": "Gemma Test",
                                "architecture": "gemma",
                                "quantization": {"name": "Q4_K_M"},
                                "size_bytes": 7_500_000_000,
                                "params_string": "12B",
                                "loaded_instances": [
                                    {"id": "google/gemma-test", "config": {}}
                                ],
                                "max_context_length": 32768,
                                "capabilities": {
                                    "vision": True,
                                    "trained_for_tool_use": False,
                                    "reasoning": {
                                        "allowed_options": ["off", "on"],
                                        "default": "off",
                                    },
                                },
                            },
                            {
                                "type": "embedding",
                                "key": "embed-test",
                                "display_name": "Embedding",
                            },
                        ]
                    }
                )
            ]
        )
        client = ModelBackendClient(http_client=http)

        discovery = client.discover(
            backend="lm_studio",
            base_url="http://localhost:1234/v1",
            api_key="secret",
        )

        self.assertEqual(discovery.api_url, "http://localhost:1234/api/v1/models")
        self.assertEqual(len(discovery.models), 1)
        model = discovery.models[0]
        self.assertEqual(model.id, "google/gemma-test")
        self.assertTrue(model.loaded)
        self.assertEqual(model.reasoning_options, ["off", "on"])
        self.assertTrue(model.vision)
        self.assertEqual(
            http.calls[0][2]["headers"]["Authorization"],
            "Bearer secret",
        )

    def test_openai_compatible_discovery_uses_v1_models(self):
        http = FakeHttpClient(
            [
                FakeResponse(
                    {
                        "object": "list",
                        "data": [
                            {"id": "qwen-local", "owned_by": "local"},
                            {"id": "gemma-local", "owned_by": "local"},
                        ],
                    }
                )
            ]
        )
        client = ModelBackendClient(http_client=http)

        discovery = client.discover(
            backend="openai_compatible",
            base_url="http://localhost:11434",
        )

        self.assertEqual(discovery.api_url, "http://localhost:11434/v1/models")
        self.assertEqual([item.id for item in discovery.models], ["gemma-local", "qwen-local"])
        self.assertFalse(discovery.models[0].loaded)

    def test_lm_studio_load_and_unload_use_native_endpoints(self):
        http = FakeHttpClient(
            [
                FakeResponse(
                    {
                        "status": "loaded",
                        "instance_id": "model-instance",
                    }
                ),
                FakeResponse({"instance_id": "model-instance"}),
            ]
        )
        client = ModelBackendClient(http_client=http)

        loaded = client.load_lm_studio_model(
            base_url="http://localhost:1234/v1",
            api_key=None,
            model="model-key",
            context_length=16384,
        )
        unloaded = client.unload_lm_studio_model(
            base_url="http://localhost:1234/v1",
            api_key=None,
            instance_id="model-instance",
        )

        self.assertEqual(loaded["status"], "loaded")
        self.assertEqual(unloaded["instance_id"], "model-instance")
        self.assertEqual(http.calls[0][1], "http://localhost:1234/api/v1/models/load")
        self.assertEqual(http.calls[0][2]["json"]["context_length"], 16384)
        self.assertEqual(http.calls[1][1], "http://localhost:1234/api/v1/models/unload")

    def test_url_helpers_and_invalid_backend(self):
        self.assertEqual(
            native_lm_studio_root("http://localhost:1234/api/v1"),
            "http://localhost:1234",
        )
        self.assertEqual(
            compatibility_base_url("http://localhost:1234"),
            "http://localhost:1234/v1",
        )
        with self.assertRaises(ModelBackendError):
            ModelBackendClient(http_client=FakeHttpClient([])).discover(
                backend="unknown",
                base_url="http://localhost:1234",
            )
        with self.assertRaises(ModelBackendError):
            compatibility_base_url("localhost:1234")


if __name__ == "__main__":
    unittest.main()
