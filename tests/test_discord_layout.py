from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCORD_ROOT = ROOT / "web" / "discord"


class DiscordLayoutTests(unittest.TestCase):
    def test_page_uses_standard_application_shell(self):
        html = (DISCORD_ROOT / "index.html").read_text(encoding="utf-8")

        for marker in (
            'class="app-shell"',
            'class="topbar"',
            'class="brand"',
            'class="header-actions"',
            'class="topnav"',
            'class="discord-layout"',
            'class="hero-card"',
            'class="discord-grid"',
            'class="settings-card"',
            'class="discord-sidebar"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_existing_discord_control_ids_are_preserved(self):
        html = (DISCORD_ROOT / "index.html").read_text(encoding="utf-8")

        for control_id in (
            "healthBadge",
            "startButton",
            "stopButton",
            "refreshButton",
            "discordForm",
            "enabledInput",
            "tokenInput",
            "allowedUsersInput",
            "saveButton",
            "deleteTokenButton",
            "gatewayStatus",
            "tokenStatus",
            "latencyStatus",
            "uptimeStatus",
            "reconnectStatus",
            "disconnectStatus",
            "notice",
            "noticeTitle",
            "noticeText",
        ):
            with self.subTest(control_id=control_id):
                self.assertIn(f'id="{control_id}"', html)

    def test_layout_uses_normal_content_grid_and_responsive_breakpoints(self):
        css = (DISCORD_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".discord-grid", css)
        self.assertIn("grid-template-columns:", css)
        self.assertIn(".discord-sidebar", css)
        self.assertIn("@media (max-width: 980px)", css)
        self.assertIn("@media (max-width: 700px)", css)


if __name__ == "__main__":
    unittest.main()
