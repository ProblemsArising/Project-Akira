from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AVATAR_ROOT = ROOT / "web" / "avatar"


class AvatarExpressionTests(unittest.TestCase):
    def test_expression_player_maps_existing_presets_to_vrm_names(self) -> None:
        source = (AVATAR_ROOT / "expressions.js").read_text(encoding="utf-8")

        for preset in (
            "soft",
            "happy",
            "playful",
            "surprised",
            "concerned",
            "annoyed",
            "shy",
        ):
            with self.subTest(preset=preset):
                self.assertIn(f"{preset}:", source)

        for name in ("happy", "relaxed", "sad", "angry", "surprised"):
            with self.subTest(name=name):
                self.assertIn(f'"{name}"', source)

    def test_renderer_smooths_face_separately_from_mouth(self) -> None:
        source = (AVATAR_ROOT / "renderer.js").read_text(encoding="utf-8")

        self.assertIn("configureFace", source)
        self.assertIn("setFaceExpression", source)
        self.assertIn("clearFaceExpression", source)
        self.assertIn("_updateFace(delta)", source)
        self.assertIn("_applyBoundValues(manager, this.faceCurrent", source)
        self.assertIn("expression.isBinary", source)
        self.assertIn("_updateMouth(delta)", source)

    def test_avatar_events_apply_and_release_reply_expression(self) -> None:
        source = (AVATAR_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("TextExpressionPlayer", source)
        self.assertIn("data.expression", source)
        self.assertIn("expressionPlayer.start", source)
        self.assertIn("expressionPlayer.complete()", source)
        self.assertIn("expressionPlayer.cancel", source)

    def test_existing_vmc_expression_backend_remains_available(self) -> None:
        source = (ROOT / "avatar" / "vmc.py").read_text(encoding="utf-8")

        self.assertIn("def set_expression_from_text", source)
        self.assertIn("EXPRESSION_PRESETS", source)
        self.assertIn("expression_from_text", source)


if __name__ == "__main__":
    unittest.main()
