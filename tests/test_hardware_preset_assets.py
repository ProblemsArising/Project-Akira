from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HardwarePresetAssetTests(unittest.TestCase):
    def test_panel_uses_editable_four_tier_controls(self):
        script = (ROOT / "web" / "hardware_presets" / "app.js").read_text(
            encoding="utf-8"
        )
        for marker in (
            '"/api/models/hardware-presets"',
            '"/api/models/hardware-presets/apply"',
            '"/api/models/hardware-presets/reset"',
            "Total VRAM",
            "GPU layers",
            "CPU threads",
            "Expected VRAM",
            "Expected RAM",
            "Set as default",
            "Restore built-in",
            "Configured llama.cpp launch flags",
            "Managed llama.cpp",
            "Requires llama.cpp",
            "inactive_backend",
            "active_backend",
            "akira:model-config-changed",
            "backend_urls",
            "rememberedUrl",
            "Off is the default",
            "MutationObserver",
            "lmStudioActions",
            "pendingBackend",
            "http://localhost:1234/v1",
            "http://localhost:11434/v1",
            "--ctx-size",
            "--n-gpu-layers",
            "--threads",
            "--parallel 1",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, script)

    def test_backend_ui_observer_uses_idempotent_attribute_updates(self):
        script = (ROOT / "web" / "hardware_presets" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("function setElementHidden(element, hidden)", script)
        self.assertIn("element.hidden !== hidden", script)
        self.assertIn("function setElementDisabled(element, disabled)", script)
        self.assertIn("element.disabled !== disabled", script)
        self.assertIn("setElementHidden(lmStudioActions, !lmStudio)", script)
        self.assertNotIn("lmStudioActions.hidden = !lmStudio", script)

    def test_non_lm_studio_backends_force_hide_lm_studio_actions(self):
        script = (ROOT / "web" / "hardware_presets" / "app.js").read_text(
            encoding="utf-8"
        )
        styles = (ROOT / "web" / "hardware_presets" / "styles.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("document.documentElement.dataset.modelBackend = backend", script)
        self.assertIn('html[data-model-backend="llama_cpp"] #lmStudioActions', styles)
        self.assertIn('html[data-model-backend="openai_compatible"] #lmStudioActions', styles)
        self.assertIn("display: none !important", styles)

    def test_backend_cards_remain_readable_in_sidebar(self):
        styles = (ROOT / "web" / "hardware_presets" / "styles.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".backend-options.has-managed-llama", styles)
        self.assertIn("grid-template-columns: 1fr", styles)
        self.assertNotIn(
            ".backend-options.has-managed-llama {\n  grid-template-columns: repeat(3",
            styles,
        )

    def test_downloaded_model_notifies_backend_selector(self):
        script = (ROOT / "web" / "model_downloads" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('CustomEvent("akira:model-config-changed"', script)
        self.assertIn("model_alias", script)
        self.assertIn("model_path", script)

    def test_api_injects_hardware_preset_assets_into_models_page(self):
        source = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
        self.assertIn('\"/static/hardware-presets\"', source)
        self.assertIn("HARDWARE_PRESETS_WEB_ROOT", source)
        self.assertIn("hardware-presets/styles.css", source)
        self.assertIn("hardware-presets/app.js", source)


if __name__ == "__main__":
    unittest.main()
