from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import numpy as np

from audio.lipsync import AudioLipSyncProfile, build_audio_lipsync_profile
from audio.tts import SynthesizedAudio, TextToSpeech


class AudioLipSyncProfileTests(unittest.TestCase):
    def test_profile_tracks_final_audio_energy_and_closes_at_end(self) -> None:
        samples = np.concatenate((
            np.zeros(800, dtype=np.float32),
            np.full(800, 0.15, dtype=np.float32),
            np.full(800, 0.8, dtype=np.float32),
        ))
        profile = build_audio_lipsync_profile(samples, 24_000, fps=30)

        self.assertEqual(profile.fps, 30)
        self.assertGreater(max(profile.values), 0.7)
        self.assertLess(profile.values[0], 0.1)
        self.assertLess(profile.values[-1], 0.2)
        self.assertEqual(profile.to_event_data()["source"], "final_audio")

    def test_profile_downmixes_multichannel_audio(self) -> None:
        stereo = np.column_stack((
            np.ones(1600, dtype=np.float32),
            np.zeros(1600, dtype=np.float32),
        ))
        profile = build_audio_lipsync_profile(stereo, 16_000)
        self.assertIsInstance(profile, AudioLipSyncProfile)
        self.assertGreater(len(profile.values), 1)


class TextToSpeechLipSyncTests(unittest.TestCase):
    def test_buffer_playback_emits_final_audio_profile(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        speaker = TextToSpeech(
            output_device=4,
            mouth_end_delay_seconds=0.0,
            on_event=lambda event_type, data: events.append((event_type, dict(data))),
        )
        audio = SynthesizedAudio(np.array([0.0, 0.5, -0.5], dtype=np.float32), 24_000)
        profile = AudioLipSyncProfile((0.0, 0.7, 0.0), 30, audio.duration_seconds)

        with (
            patch.object(speaker, "render", return_value=audio),
            patch.object(speaker, "play_audio") as play_audio,
            patch("audio.tts.build_audio_lipsync_profile", return_value=profile),
            patch("audio.tts.start_talking_audio") as start_audio,
            patch("audio.tts.stop_talking") as stop_talking,
            patch("audio.tts.time.sleep"),
        ):
            speaker.speak("Converted speech")

        self.assertEqual(events[0][0], "avatar.lipsync.started")
        self.assertEqual(events[-1][0], "avatar.lipsync.stopped")
        self.assertEqual(events[0][1]["values"], [0.0, 0.7, 0.0])
        start_audio.assert_called_once_with(profile.values, 30, text="Converted speech")
        play_audio.assert_called_once_with(audio)
        stop_talking.assert_called_once_with()

    def test_default_sapi_path_emits_explicit_text_fallback(self) -> None:
        callback = Mock()
        speaker = TextToSpeech(on_event=callback, mouth_end_delay_seconds=0.0)
        engine = Mock()
        engine.getProperty.return_value = []

        with (
            patch("audio.tts.pyttsx3.init", return_value=engine),
            patch("audio.tts.start_talking"),
            patch("audio.tts.stop_talking"),
            patch("audio.tts.time.sleep"),
        ):
            speaker.speak("Fallback speech")

        callback.assert_any_call(
            "avatar.lipsync.text",
            {"text": "Fallback speech", "source": "tts_text"},
        )
        callback.assert_called_with("avatar.lipsync.stopped", {})


class AvatarAudioAssetTests(unittest.TestCase):
    def test_audio_viseme_player_uses_waveform_events(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        source = (root / "web" / "avatar" / "audio_visemes.js").read_text(encoding="utf-8")
        self.assertIn("AudioVisemePlayer", source)
        self.assertIn("requestAnimationFrame", source)
        self.assertIn("setMouthVisemes", source)
        self.assertIn("profile.values", source)

        app_source = (root / "web" / "avatar" / "app.js").read_text(encoding="utf-8")
        self.assertIn("AudioVisemePlayer", app_source)
        self.assertIn('case "avatar.lipsync.started"', app_source)
        self.assertIn('case "avatar.lipsync.text"', app_source)
        self.assertIn('case "avatar.lipsync.stopped"', app_source)


if __name__ == "__main__":
    unittest.main()
