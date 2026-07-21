from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

THEMED_PAGES = (
    ROOT / "web" / "chat" / "index.html",
    ROOT / "web" / "history" / "index.html",
    ROOT / "web" / "personalities" / "index.html",
    ROOT / "web" / "models" / "index.html",
    ROOT / "web" / "audio" / "index.html",
    ROOT / "web" / "settings" / "index.html",
)

THEME_LINK = (
    '<link rel="stylesheet" '
    'href="/static/discord/site-theme.css">'
)


class WebThemeTests(unittest.TestCase):
    def test_every_primary_page_loads_shared_theme_after_page_styles(self):
        for page in THEMED_PAGES:
            with self.subTest(page=page):
                html = page.read_text(encoding="utf-8")
                self.assertIn(THEME_LINK, html)

                page_styles = html.index("/styles.css")
                shared_theme = html.index("/static/discord/site-theme.css")
                self.assertGreater(shared_theme, page_styles)

    def test_shared_theme_uses_discord_palette(self):
        theme = (
            ROOT / "web" / "discord" / "site-theme.css"
        ).read_text(encoding="utf-8")

        for value in (
            "#0a0c12",
            "#121620",
            "#181d29",
            "#2b3242",
            "#8b7cff",
            "#a99eff",
            "#46d38a",
            "#f0bd5a",
            "#ff7272",
        ):
            with self.subTest(value=value):
                self.assertIn(value, theme)

    def test_shared_theme_does_not_own_page_layout(self):
        theme = (
            ROOT / "web" / "discord" / "site-theme.css"
        ).read_text(encoding="utf-8")

        forbidden = (
            "grid-template",
            "position: fixed",
            "position: absolute",
            "width:",
            "height:",
            "margin:",
            "padding:",
        )
        for declaration in forbidden:
            with self.subTest(declaration=declaration):
                self.assertNotIn(declaration, theme)


if __name__ == "__main__":
    unittest.main()
