from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from audio.calibration import AudioCalibrationSession, CalibrationCancelled
from config.settings import AppSettings


class FakeInputStream:
    def __init__(self, *, quiet_reads: int = 5, **kwargs) -> None:
        self.quiet_reads = quiet_reads
        self.read_count = 0
        self.channels = int(kwargs.get("channels", 1))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, frame_size: int):
        amplitude = 0.001 if self.read_count < self.quiet_reads else 0.1
        self.read_count += 1
        return (
            np.full((frame_size, self.channels), amplitude, dtype=np.float32),
            False,
        )


class FakeSoundDevice:
    def check_input_settings(self, **kwargs):
        return None

    def InputStream(self, **kwargs):
        return FakeInputStream(**kwargs)


class AudioCalibrationTests(unittest.TestCase):
    def test_calibration_measures_noise_saves_speech_and_emits_levels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sample.wav"
            settings = AppSettings()
            settings.audio.sample_rate = 1000
            settings.audio.frame_ms = 100
            settings.audio.channels = 1
            settings.audio.calibration_seconds = 0.5
            settings.audio.start_threshold_multiplier = 6.0
            settings.audio.end_threshold_multiplier = 1.8

            events = []
            result = AudioCalibrationSession(
                settings=settings,
                input_device=None,
                output_file=output,
                sd_module=FakeSoundDevice(),
            ).run(
                duration_seconds=2.0,
                calibration_seconds=0.5,
                on_level=events.append,
            )

            self.assertTrue(output.exists())
            rate, audio = wavfile.read(output)
            self.assertEqual(rate, 1000)
            self.assertGreater(len(audio), 0)
            self.assertAlmostEqual(result.noise_floor, 0.001, places=4)
            self.assertGreater(result.peak_level, 0.09)
            self.assertGreater(result.suggested_min_start_threshold, 0)
            self.assertLess(
                result.suggested_min_end_threshold,
                result.suggested_min_start_threshold,
            )
            self.assertEqual(events[0]["phase"], "noise")
            self.assertEqual(events[-1]["phase"], "speech")
            self.assertAlmostEqual(events[-1]["progress"], 1.0)

    def test_cancelled_session_does_not_open_microphone(self) -> None:
        session = AudioCalibrationSession(
            settings=AppSettings(),
            input_device=None,
            sd_module=FakeSoundDevice(),
        )
        session.request_stop()
        with self.assertRaises(CalibrationCancelled):
            session.run(duration_seconds=2.0)


if __name__ == "__main__":
    unittest.main()
