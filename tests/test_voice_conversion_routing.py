from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from audio.tts import SynthesizedAudio, TextToSpeech


ROOT = Path(__file__).resolve().parents[1]


class VoiceConversionRoutingTests(unittest.TestCase):
    def test_converted_audio_plays_directly_to_selected_output(self) -> None:
        source = SynthesizedAudio(
            samples=np.array([0.0, 0.1, -0.1], dtype=np.float32),
            sample_rate=24_000,
        )
        converted = SynthesizedAudio(
            samples=np.array([0.0, 0.4, -0.4], dtype=np.float32),
            sample_rate=48_000,
        )
        converter = Mock()
        converter.convert.return_value = converted
        profile = SimpleNamespace(
            values=(0.0, 0.7, 0.0),
            fps=30,
            to_event_data=lambda: {"values": [0.0, 0.7, 0.0], "fps": 30},
        )
        speaker = TextToSpeech(
            output_device=17,
            audio_converter=converter,
            mouth_end_delay_seconds=0,
        )

        with (
            patch.object(speaker, "synthesize", return_value=source),
            patch("audio.tts.build_audio_lipsync_profile", return_value=profile),
            patch("audio.tts.start_talking_audio"),
            patch("audio.tts.stop_talking"),
            patch(
                "audio.tts._prepare_output_audio",
                return_value=(converted.samples, converted.sample_rate),
            ),
            patch("audio.tts.sd.play", create=True) as play,
        ):
            speaker.speak("Direct converted speech")

        converter.convert.assert_called_once_with(source)
        play.assert_called_once()
        args, kwargs = play.call_args
        np.testing.assert_array_equal(args[0], converted.samples)
        self.assertEqual(
            kwargs,
            {
                "samplerate": converted.sample_rate,
                "device": 17,
                "blocking": True,
            },
        )

    def test_documentation_does_not_require_external_audio_routing(self) -> None:
        documents = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
        combined = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in documents
        ).lower()

        forbidden_instructions = (
            r"\b(?:install|download)\s+(?:the\s+)?vb-?cable\b",
            r"\bvb-?cable\s+(?:is\s+)?required\b",
            r"(?<!not )(?<!never )\brequires?\s+(?:the\s+)?vb-?cable\b",
            r"\bset\s+(?:the\s+)?output\s+to\s+cable input\b",
        )
        for pattern in forbidden_instructions:
            self.assertIsNone(re.search(pattern, combined), pattern)

        self.assertIn("does not require vb-cable", combined)
        self.assertIn("direct playback", combined)


if __name__ == "__main__":
    unittest.main()
