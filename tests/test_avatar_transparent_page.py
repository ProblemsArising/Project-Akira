from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AvatarTransparentPageTests(unittest.TestCase):
    def test_avatar_page_enables_transparent_mode_from_query_parameter(self):
        script = (ROOT / "web" / "avatar" / "app.js").read_text(encoding="utf-8")

        self.assertIn('get("transparent") === "1"', script)
        self.assertIn('"transparent-avatar-window"', script)

    def test_transparent_mode_is_a_clean_display_only_overlay(self):
        css = (ROOT / "web" / "avatar" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("html.transparent-avatar-window", css)
        self.assertIn("background: transparent !important", css)
        self.assertIn(".renderer-host canvas", css)
        self.assertIn("body.transparent-avatar-window .stage", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn("body.transparent-avatar-window .stage-header", css)
        self.assertIn("body.transparent-avatar-window .stage-footer", css)
        self.assertIn("visibility: hidden !important", css)
        self.assertIn("opacity: 0 !important", css)
        self.assertNotIn("display: none !important", css)
        self.assertNotIn(
            "body.transparent-avatar-window .stage-header:hover",
            css,
        )

    def test_settings_page_exposes_transparent_avatar_option(self):
        script = (ROOT / "web" / "settings" / "app.js").read_text(encoding="utf-8")

        self.assertIn('"general.avatar_transparent_window"', script)
        self.assertIn("Transparent avatar window", script)
        self.assertIn("display-only overlay", script)
        self.assertIn("passes mouse input through", script)
        self.assertIn("Choose or remove the VRM in opaque mode", script)


if __name__ == "__main__":
    unittest.main()
