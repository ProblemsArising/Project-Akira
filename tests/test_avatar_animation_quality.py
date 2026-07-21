from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AVATAR_ROOT = ROOT / "web" / "avatar"


class AvatarAnimationQualityTests(unittest.TestCase):
    def test_visemes_are_syllable_timed_instead_of_looping_per_character(self) -> None:
        source = (AVATAR_ROOT / "visemes.js").read_text(encoding="utf-8")

        self.assertIn("estimateSpeechDurationMs", source)
        self.assertIn("buildWeightedUnits", source)
        self.assertIn("speechRate", source)
        self.assertIn("vowelGroups", source)
        self.assertIn("this.eventIndex >= this.events.length", source)
        self.assertNotIn("% this.events.length", source)
        self.assertIn("Math.min(legacyDelayMs, 180)", source)

    def test_expression_presets_are_visible_on_binary_vrm_clips(self) -> None:
        source = (AVATAR_ROOT / "expressions.js").read_text(encoding="utf-8")
        renderer = (AVATAR_ROOT / "renderer.js").read_text(encoding="utf-8")

        self.assertIn("happy: 0.86", source)
        self.assertIn("surprised: 0.92", source)
        self.assertIn("angry: 0.76", source)
        self.assertIn("expression.isBinary", renderer)
        self.assertIn("normalized > 0.08 ? 1 : 0", renderer)

    def test_renderer_resolves_vrm0_and_vrm1_expression_aliases(self) -> None:
        source = (AVATAR_ROOT / "renderer.js").read_text(encoding="utf-8")

        for alias in ("joy", "fun", "sorrow", "surprise"):
            with self.subTest(alias=alias):
                self.assertIn(f'"{alias}"', source)
        self.assertIn("expressionMap", source)
        self.assertIn("getExpressionCapabilities", source)

    def test_avatar_uses_tts_rate_and_reports_missing_face_presets(self) -> None:
        source = (AVATAR_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("tts_rate: rootSettings.tts", source)
        self.assertIn("getExpressionCapabilities", source)
        self.assertIn("no face presets", source)
        self.assertIn('case "settings.updated"', source)


if __name__ == "__main__":
    unittest.main()
