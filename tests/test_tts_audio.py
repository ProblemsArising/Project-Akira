from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import scipy.io.wavfile as wav

from audio.tts import DirectAudioPlayer, SynthesizedAudio, TTSPlaybackError, TextToSpeech


class SynthesizedAudioTests(unittest.TestCase):
    def test_normalizes_samples_and_exposes_audio_metadata(self) -> None:
        audio = SynthesizedAudio(
            samples=np.array([-32768, 0, 32767], dtype=np.int16),
            sample_rate=16_000,
        )

        self.assertEqual(audio.samples.dtype, np.float32)
        self.assertTrue(audio.samples.flags.c_contiguous)
        self.assertEqual(audio.frames, 3)
        self.assertEqual(audio.channels, 1)
        self.assertAlmostEqual(audio.duration_seconds, 3 / 16_000)
        np.testing.assert_allclose(
            audio.samples,
            np.array([-1.0, 0.0, 32767 / 32768], dtype=np.float32),
        )

    def test_wav_helpers_produce_pcm16_audio(self) -> None:
        audio = SynthesizedAudio(
            samples=np.array([-1.0, 0.0, 1.0], dtype=np.float32),
            sample_rate=22_050,
        )

        sample_rate, encoded = wav.read(BytesIO(audio.to_wav_bytes()))
        self.assertEqual(sample_rate, 22_050)
        self.assertEqual(encoded.dtype, np.int16)
        np.testing.assert_array_equal(
            encoded,
            np.array([-32767, 0, 32767], dtype=np.int16),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "nested" / "speech.wav"
            written = audio.write_wav(destination)
            self.assertEqual(written, destination)
            self.assertTrue(destination.exists())
            file_rate, file_audio = wav.read(str(destination))

        self.assertEqual(file_rate, 22_050)
        np.testing.assert_array_equal(file_audio, encoded)


class DirectAudioPlayerTests(unittest.TestCase):
    def test_system_default_playback_uses_prepared_audio_buffer(self) -> None:
        audio = SynthesizedAudio(
            samples=np.array([0.0, 0.25, -0.25], dtype=np.float32),
            sample_rate=40_000,
        )
        playback = np.array([0.0, 0.5, -0.5], dtype=np.float32)

        with (
            patch(
                "audio.tts._prepare_output_audio",
                return_value=(playback, 48_000),
            ) as prepare,
            patch("audio.tts.sd.play", create=True) as play,
        ):
            DirectAudioPlayer().play(audio)

        prepare.assert_called_once_with(audio.samples, 40_000, None)
        play.assert_called_once_with(
            playback,
            samplerate=48_000,
            device=None,
            blocking=True,
        )

    def test_playback_backend_failures_are_wrapped(self) -> None:
        audio = SynthesizedAudio(
            samples=np.array([0.0, 0.1], dtype=np.float32),
            sample_rate=24_000,
        )

        with (
            patch(
                "audio.tts._prepare_output_audio",
                return_value=(audio.samples, audio.sample_rate),
            ),
            patch(
                "audio.tts.sd.play",
                side_effect=RuntimeError("device vanished"),
                create=True,
            ),
        ):
            with self.assertRaisesRegex(
                TTSPlaybackError,
                "system default output device.*device vanished",
            ):
                DirectAudioPlayer().play(audio)


class TextToSpeechSynthesisTests(unittest.TestCase):
    def test_synthesize_returns_buffer_and_removes_temporary_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            wav_path = Path(temporary_directory) / "temporary.wav"
            wav.write(
                str(wav_path),
                24_000,
                np.array([-32768, 0, 32767], dtype=np.int16),
            )
            speaker = TextToSpeech(voice_index=2, rate=190, volume=0.75)

            with patch("audio.tts._synthesize_to_wav", return_value=wav_path) as synthesize:
                result = speaker.synthesize("  Hello, Akira.  ")

            self.assertFalse(wav_path.exists())

        self.assertIsInstance(result, SynthesizedAudio)
        assert result is not None
        self.assertEqual(result.sample_rate, 24_000)
        self.assertEqual(result.samples.dtype, np.float32)
        synthesize.assert_called_once_with(
            "Hello, Akira.",
            voice_index=2,
            rate=190,
            volume=0.75,
        )

    def test_blank_synthesis_does_not_initialize_tts(self) -> None:
        speaker = TextToSpeech()

        with patch("audio.tts._synthesize_to_wav") as synthesize:
            self.assertIsNone(speaker.synthesize("   "))
            self.assertIsNone(speaker.synthesize_to_wav("\n", "ignored.wav"))

        synthesize.assert_not_called()

    def test_synthesize_to_wav_uses_caller_destination(self) -> None:
        speaker = TextToSpeech(voice_index=3, rate=205, volume=0.6)

        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "exports" / "akira.wav"
            with patch(
                "audio.tts._synthesize_to_wav",
                return_value=destination,
            ) as synthesize:
                result = speaker.synthesize_to_wav("  Test line  ", destination)

        self.assertEqual(result, destination)
        synthesize.assert_called_once_with(
            "Test line",
            voice_index=3,
            rate=205,
            volume=0.6,
            destination=destination,
        )

    def test_selected_device_playback_uses_synthesized_buffer(self) -> None:
        speaker = TextToSpeech(output_device=7, mouth_end_delay_seconds=0.0)
        synthesized = SynthesizedAudio(
            samples=np.array([0.0, 0.25, -0.25], dtype=np.float32),
            sample_rate=24_000,
        )
        playback = np.array([0.0, 0.5, -0.5], dtype=np.float32)
        profile = Mock()
        profile.values = (0.0, 0.7, 0.0)
        profile.fps = 30
        profile.to_event_data.return_value = {
            "fps": 30,
            "duration_seconds": 0.01,
            "values": [0.0, 0.7, 0.0],
            "source": "final_audio",
        }

        with (
            patch.object(speaker, "synthesize", return_value=synthesized) as synthesize,
            patch(
                "audio.tts._prepare_output_audio",
                return_value=(playback, 48_000),
            ) as prepare,
            patch("audio.tts.build_audio_lipsync_profile", return_value=profile),
            patch("audio.tts.start_talking_audio") as start_talking_audio,
            patch("audio.tts.stop_talking") as stop_talking,
            patch("audio.tts.sd.play", create=True) as play,
            patch("audio.tts.time.sleep"),
        ):
            speaker.speak("  Routed speech  ")

        synthesize.assert_called_once_with("Routed speech")
        prepare.assert_called_once_with(synthesized.samples, 24_000, 7)
        start_talking_audio.assert_called_once_with(
            profile.values, profile.fps, text="Routed speech"
        )
        play.assert_called_once_with(
            playback,
            samplerate=48_000,
            device=7,
            blocking=True,
        )
        stop_talking.assert_called_once_with()

    def test_converter_pipeline_plays_final_converted_buffer(self) -> None:
        source = SynthesizedAudio(
            samples=np.array([0.0, 0.1, -0.1], dtype=np.float32),
            sample_rate=24_000,
        )
        converted = SynthesizedAudio(
            samples=np.array([0.0, 0.8, -0.8], dtype=np.float32),
            sample_rate=40_000,
        )
        profile = Mock()
        profile.values = (0.0, 0.7, 0.0)
        profile.fps = 30
        profile.to_event_data.return_value = {
            "fps": 30,
            "duration_seconds": 0.01,
            "values": [0.0, 0.7, 0.0],
            "source": "final_audio",
        }
        converter = Mock()
        converter.convert.return_value = converted
        speaker = TextToSpeech(
            audio_converter=converter,
            mouth_end_delay_seconds=0.0,
        )

        with (
            patch.object(speaker, "synthesize", return_value=source) as synthesize,
            patch.object(speaker, "play_audio") as play_audio,
            patch("audio.tts.build_audio_lipsync_profile", return_value=profile),
            patch("audio.tts.start_talking_audio") as start_talking_audio,
            patch("audio.tts.stop_talking") as stop_talking,
            patch("audio.tts.pyttsx3.init") as initialize_engine,
            patch("audio.tts.time.sleep"),
        ):
            speaker.speak("  Converted speech  ")

        synthesize.assert_called_once_with("Converted speech")
        converter.convert.assert_called_once_with(source)
        play_audio.assert_called_once_with(converted)
        initialize_engine.assert_not_called()
        start_talking_audio.assert_called_once_with(
            profile.values, profile.fps, text="Converted speech"
        )
        stop_talking.assert_called_once_with()

    def test_render_rejects_invalid_converter_results(self) -> None:
        source = SynthesizedAudio(
            samples=np.array([0.0, 0.1], dtype=np.float32),
            sample_rate=24_000,
        )
        converter = Mock()
        converter.convert.return_value = np.zeros(2, dtype=np.float32)
        speaker = TextToSpeech(audio_converter=converter)

        with patch.object(speaker, "synthesize", return_value=source):
            with self.assertRaisesRegex(
                TTSPlaybackError,
                "expected SynthesizedAudio",
            ):
                speaker.render("Hello")


if __name__ == "__main__":
    unittest.main()
