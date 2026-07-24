from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AVATAR_ROOT = ROOT / "web" / "avatar"


class AvatarVisemeTests(unittest.TestCase):
    def test_viseme_player_uses_vrm_mouth_preset_names(self) -> None:
        source = (AVATAR_ROOT / "visemes.js").read_text(encoding="utf-8")

        for name in ("aa", "ih", "ou", "ee", "oh"):
            with self.subTest(name=name):
                self.assertIn(f'"{name}"', source)

        self.assertIn("buildVisemeEvents", source)
        self.assertIn("TextVisemePlayer", source)
        self.assertIn("mouth_start_delay_seconds", source)
        self.assertIn("mouth_attack_speed", source)
        self.assertIn("mouth_release_speed", source)

    def test_renderer_applies_and_smooths_mouth_expressions(self) -> None:
        source = (AVATAR_ROOT / "renderer.js").read_text(encoding="utf-8")

        self.assertIn("setMouthVisemes", source)
        self.assertIn("closeMouth", source)
        self.assertIn("expressionManager", source)
        self.assertIn("_applyBoundValues(manager, this.mouthCurrent", source)
        self.assertIn("_updateMouth(delta)", source)

    def test_avatar_events_start_and_stop_visemes_around_tts(self) -> None:
        source = (AVATAR_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn('case "chat.reply_ready"', source)
        self.assertIn('case "avatar.lipsync.started"', source)
        self.assertIn("audioVisemePlayer.start(data)", source)
        self.assertIn('case "avatar.lipsync.text"', source)
        self.assertIn('visemePlayer.start(data.text || "")', source)
        self.assertIn('case "avatar.lipsync.stopped"', source)
        self.assertIn('case "chat.completed"', source)
        self.assertIn('case "chat.failed"', source)
        self.assertIn("visemePlayer.stop()", source)
        self.assertIn('fetch("/api/settings"', source)

    def test_issue_24_does_not_remove_vmc_backend(self) -> None:
        source = (ROOT / "audio" / "tts.py").read_text(encoding="utf-8")

        self.assertIn("from avatar.vmc import start_talking, stop_talking", source)
        self.assertIn("start_talking(text)", source)
        self.assertIn("stop_talking()", source)


if __name__ == "__main__":
    unittest.main()
