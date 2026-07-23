from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LlamaCppRuntimeAssetTests(unittest.TestCase):
    def test_models_page_exposes_managed_runtime_controls(self):
        script = (ROOT / "web" / "model_downloads" / "app.js").read_text(
            encoding="utf-8"
        )
        for marker in (
            "Runtime installer",
            "Install selected runtime",
            "Repair or replace runtime",
            "Remove managed runtime",
            "/api/llama-cpp/runtime/install",
            "/api/llama-cpp/runtime/cancel",
            'method: "DELETE"',
            "llama-server.exe --list-devices",
            "automatically",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, script)

    def test_runtime_panel_is_full_width_and_responsive(self):
        styles = (ROOT / "web" / "model_downloads" / "styles.css").read_text(
            encoding="utf-8"
        )
        for marker in (
            ".runtime-manager-card",
            "min-width: 0",
            ".runtime-controls",
            ".runtime-details",
            "@media (max-width: 820px)",
            "@media (max-width: 620px)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, styles)

    def test_api_and_backend_use_managed_runtime_manager(self):
        api = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
        backend = (ROOT / "ai" / "llama_cpp_backend.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            "LlamaCppRuntimeManager",
            '"/api/llama-cpp/runtime"',
            '"/api/llama-cpp/runtime/install"',
            '"/api/llama-cpp/runtime/cancel"',
            "llama_cpp_runtime.shutdown()",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, api)
        self.assertIn("find_managed_llama_cpp_executable", backend)
        self.assertIn("is_managed_llama_cpp_path", backend)


if __name__ == "__main__":
    unittest.main()
