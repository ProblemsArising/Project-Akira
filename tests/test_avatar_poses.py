from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AVATAR_ROOT = ROOT / "web" / "avatar"


class AvatarBodyPoseTests(unittest.TestCase):
    def test_pose_module_defines_standing_and_emotional_presets(self) -> None:
        source = (AVATAR_ROOT / "poses.js").read_text(encoding="utf-8")

        self.assertIn("STANDING_BODY_POSE", source)
        self.assertIn("leftUpperArm: { z: 68 }", source)
        self.assertIn("rightUpperArm: { z: -68 }", source)
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

    def test_pose_module_uses_existing_avatar_pose_settings(self) -> None:
        source = (AVATAR_ROOT / "poses.js").read_text(encoding="utf-8")

        for setting in (
            "standing_pose_replay_enabled",
            "body_expressions_enabled",
            "pose_fps",
            "body_expression_strength",
            "body_pose_strength",
            "body_expression_fade_speed",
            "arm_gesture_strength",
            "arm_bone_rotation_strength",
            "disable_idle_during_expressions",
            "idle_strength_during_expressions",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, source)

    def test_renderer_layers_body_pose_and_idle_on_normalized_bones(self) -> None:
        source = (AVATAR_ROOT / "renderer.js").read_text(encoding="utf-8")

        self.assertIn("EmbeddedBodyPose", source)
        self.assertIn("getBodyPoseCapabilities", source)
        self.assertIn("leftHand", source)
        self.assertIn("rightHand", source)
        self.assertIn("const poseSample = this.bodyPose.update(delta)", source)
        self.assertIn("const idleScale = this.bodyPose.idleScale()", source)
        self.assertIn("poseOffset.x + idleOffset.x * idleScale", source)
        self.assertIn("getNormalizedBoneNode", source)

    def test_avatar_events_start_complete_and_cancel_body_poses(self) -> None:
        source = (AVATAR_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("TextBodyPosePlayer", source)
        self.assertIn("bodyPosePlayer.configure(embeddedSettings)", source)
        self.assertIn("bodyPosePlayer.start(", source)
        self.assertIn("bodyPosePlayer.complete()", source)
        self.assertGreaterEqual(source.count("bodyPosePlayer.cancel("), 4)
        self.assertIn("· poses", source)

    def test_existing_vmc_body_expression_backend_remains_available(self) -> None:
        source = (ROOT / "avatar" / "vmc.py").read_text(encoding="utf-8")

        self.assertIn("BODY_EXPRESSION_PRESETS", source)
        self.assertIn("def set_body_expression", source)
        self.assertIn("def clear_body_expression", source)


if __name__ == "__main__":
    unittest.main()
