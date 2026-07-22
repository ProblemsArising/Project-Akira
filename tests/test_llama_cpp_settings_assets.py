from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LlamaCppSettingsAssetTests(unittest.TestCase):
    def test_settings_page_exposes_managed_llama_cpp_fields(self):
        script = (ROOT / "web" / "settings" / "app.js").read_text(
            encoding="utf-8"
        )

        for marker in (
            'value: "llama_cpp"',
            '"llm.llama_cpp_executable"',
            '"llm.llama_cpp_model_path"',
            '"llm.llama_cpp_model_alias"',
            '"llm.llama_cpp_port"',
            '"llm.llama_cpp_context_size"',
            '"llm.llama_cpp_gpu_layers"',
            '"llm.llama_cpp_threads"',
            '"llm.llama_cpp_startup_timeout_seconds"',
            '"llm.llama_cpp_extra_args"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, script)


if __name__ == "__main__":
    unittest.main()
