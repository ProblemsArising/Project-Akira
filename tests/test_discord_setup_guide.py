from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCORD_ROOT = ROOT / "web" / "discord"


class DiscordSetupGuideTests(unittest.TestCase):
    def test_page_contains_complete_first_time_setup_guide(self):
        html = (DISCORD_ROOT / "index.html").read_text(encoding="utf-8")

        required = (
            'href="/static/discord/setup-guide.css"',
            'class="setup-guide"',
            "Create your Discord bot",
            "Open Discord Developer Portal",
            "Create the application and token",
            "Configure installation",
            "Add Akira to Discord",
            "Copy your Discord user ID",
            "Connect Project Akira",
            "Guild Install",
            "Discord Provided Link",
            "applications.commands",
            "Send Messages",
            "Developer Mode",
            "Keep the bot token private",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_setup_guide_uses_official_external_links_safely(self):
        html = (DISCORD_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn(
            'href="https://discord.com/developers/applications"',
            html,
        )
        self.assertIn(
            "https://docs.discord.com/developers/quick-start/getting-started",
            html,
        )
        self.assertIn(
            "https://support.discord.com/hc/en-us/articles/"
            "206346498-Where-can-I-find-my-User-Server-Message-ID",
            html,
        )
        self.assertEqual(html.count('target="_blank"'), 3)
        self.assertEqual(html.count('rel="noopener noreferrer"'), 3)

    def test_setup_guide_is_responsive_and_does_not_require_javascript(self):
        css = (DISCORD_ROOT / "setup-guide.css").read_text(encoding="utf-8")
        html = (DISCORD_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn(".setup-guide", css)
        self.assertIn(".setup-steps", css)
        self.assertIn("@media (max-width: 700px)", css)
        self.assertIn("<details", html)
        self.assertIn("<summary>", html)
        self.assertNotIn("setupGuideButton", html)


if __name__ == "__main__":
    unittest.main()
