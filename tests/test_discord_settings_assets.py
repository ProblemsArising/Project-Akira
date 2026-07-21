from __future__ import annotations

import unittest
from pathlib import Path


class DiscordSettingsAssetTests(unittest.TestCase):
    def test_page_contains_secure_token_and_access_controls(self):
        root = Path(__file__).resolve().parents[1] / "web" / "discord"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")

        self.assertIn('type="password"', html)
        self.assertIn("Allowed Discord user IDs", html)
        self.assertIn("Windows Credential Manager", html)
        self.assertIn("/api/discord/settings", script)
        self.assertIn("/api/discord/start", script)
        self.assertIn("/api/discord/stop", script)
        self.assertIn("/api/discord/token", script)


if __name__ == "__main__":
    unittest.main()
