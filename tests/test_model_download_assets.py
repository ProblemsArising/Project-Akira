from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ModelDownloadAssetTests(unittest.TestCase):
    def test_download_panel_uses_expected_api_routes(self):
        script = (ROOT / "web" / "model_downloads" / "app.js").read_text(
            encoding="utf-8"
        )
        for marker in (
            '"/api/models/downloads"',
            "/api/models/downloads/select",
            "/api/models/downloads/local/",
            "/cancel",
            "Direct GGUF URL",
            "SHA-256",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, script)

    def test_api_injects_download_assets_into_models_page(self):
        source = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
        self.assertIn('"/static/model-downloads"', source)
        self.assertIn("MODEL_DOWNLOADS_WEB_ROOT", source)
        self.assertIn("model-downloads/styles.css", source)
        self.assertIn("model-downloads/app.js", source)


if __name__ == "__main__":
    unittest.main()
