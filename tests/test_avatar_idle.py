from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AVATAR_ROOT = ROOT / "web" / "avatar"


class AvatarIdleTests(unittest.TestCase):
    def test_idle_module_uses_existing_avatar_motion_settings(self) -> None:
        source = (AVATAR_ROOT / "idle.js").read_text(encoding="utf-8")

        for setting in (
            "auto_start_idle",
            "pose_fps",
            "body_idle_strength",
            "body_root_bob_meters",
            "body_sway_meters",
            "body_breath_degrees",
            "body_head_yaw_degrees",
            "body_arm_sway_degrees",
            "body_speaking_motion_boost",
            "body_talk_pulse_degrees",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, source)

    def test_idle_motion_is_layered_and_speaking_adds_energy(self) -> None:
        source = (AVATAR_ROOT / "idle.js").read_text(encoding="utf-8")

        for frequency in ("1.08", "0.49", "0.47", "0.21", "0.31", "0.13"):
            with self.subTest(frequency=frequency):
                self.assertIn(f"time * {frequency}", source)
        self.assertIn("speakingMotionBoost", source)
        self.assertIn("const talk = speaking", source)
        self.assertIn("leftUpperArm", source)
        self.assertIn("rightUpperArm", source)
        self.assertIn("-armSway * 0.55 * drift", source)

    def test_renderer_applies_idle_to_normalized_vrm_bones(self) -> None:
        source = (AVATAR_ROOT / "renderer.js").read_text(encoding="utf-8")

        self.assertIn("EmbeddedIdleMotion", source)
        self.assertIn("getNormalizedBoneNode", source)
        self.assertIn("_captureIdleRig", source)
        self.assertIn("_updateIdleMovement(delta)", source)
        self.assertIn("binding.node.quaternion", source)
        self.assertIn("root.node.position.set", source)
        self.assertIn("_resetIdleRig", source)

    def test_avatar_events_configure_idle_and_speaking_boost(self) -> None:
        source = (AVATAR_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("embeddedRenderer.configureIdle(settings)", source)
        self.assertIn("embeddedRenderer.getIdleCapabilities()", source)
        self.assertIn("embeddedRenderer.setSpeaking(Boolean(data.will_speak))", source)
        self.assertGreaterEqual(
            source.count("embeddedRenderer.setSpeaking(false)"),
            4,
        )
        self.assertIn("· idle", source)

    def test_existing_vmc_idle_backend_remains_available(self) -> None:
        source = (ROOT / "avatar" / "vmc.py").read_text(encoding="utf-8")

        self.assertIn("def start_idle", source)
        self.assertIn("def stop_idle", source)
        self.assertIn("_standing_pose_with_idle", source)


if __name__ == "__main__":
    unittest.main()
