from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MAIN_PAGES = (
    ROOT / "web" / "chat" / "index.html",
    ROOT / "web" / "history" / "index.html",
    ROOT / "web" / "personalities" / "index.html",
    ROOT / "web" / "models" / "index.html",
    ROOT / "web" / "audio" / "index.html",
    ROOT / "web" / "settings" / "index.html",
)

EXPECTED_LINKS = (
    'href="/"',
    'href="/history"',
    'href="/personalities"',
    'href="/models"',
    'href="/audio"',
    'href="/discord"',
    'href="/settings"',
)


class NavigationTests(unittest.TestCase):
    def test_every_main_page_links_to_discord(self):
        for page in MAIN_PAGES:
            with self.subTest(page=page):
                html = page.read_text(encoding="utf-8")
                self.assertIn(
                    '<a href="/discord">Discord</a>',
                    html,
                )

    def test_discord_page_links_to_every_main_section(self):
        page = ROOT / "web" / "discord" / "index.html"
        html = page.read_text(encoding="utf-8")

        for link in EXPECTED_LINKS:
            with self.subTest(link=link):
                self.assertIn(link, html)

        self.assertIn(
            '<a class="active" href="/discord">Discord</a>',
            html,
        )


if __name__ == "__main__":
    unittest.main()
