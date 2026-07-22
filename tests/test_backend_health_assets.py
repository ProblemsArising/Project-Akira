from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BackendHealthAssetTests(unittest.TestCase):
    def test_models_panel_checks_backend_and_model_readiness(self):
        script = (ROOT / "web" / "backend_health" / "app.js").read_text(
            encoding="utf-8"
        )
        for marker in (
            '"/api/models/health"',
            "Backend health",
            "Model readiness",
            "Managed process",
            "Response time",
            "Check now",
            "healthy",
            "degraded",
            "offline",
            "idle",
            "misconfigured",
            "REFRESH_INTERVAL_MS",
            "document.visibilityState",
            "#baseUrlInput",
            "#apiKeyInput",
            "#modelInput",
            ".backend-option.selected[data-backend]",
            "external backend",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker.casefold(), script.casefold())

    def test_panel_is_responsive_and_does_not_force_fixed_width(self):
        styles = (ROOT / "web" / "backend_health" / "styles.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".backend-health-card", styles)
        self.assertIn("width: 100%", styles)
        self.assertIn("min-width: 0", styles)
        self.assertIn("@media (max-width: 980px)", styles)
        self.assertIn("overflow-wrap: anywhere", styles)

    def test_api_mounts_routes_and_assets(self):
        source = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
        for marker in (
            "BACKEND_HEALTH_WEB_ROOT",
            '"/static/backend-health"',
            "backend-health/styles.css",
            "backend-health/app.js",
            '"/api/models/health"',
            "BackendHealthRequest",
            "BackendHealthResponse",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_documentation_explains_idle_managed_backend(self):
        documentation = (ROOT / "docs" / "backend_health.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("never send a chat prompt", documentation)
        self.assertIn("lazy process is idle", documentation)
        self.assertIn("another program is responding", documentation)


if __name__ == "__main__":
    unittest.main()
