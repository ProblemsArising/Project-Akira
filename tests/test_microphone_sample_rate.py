from __future__ import annotations

import sys
import types
import unittest

# The patch-builder environment may not include PortAudio/sounddevice. The real
# application dependency is installed from requirements.txt; this stub only
# lets the pure sample-rate selection helper be unit tested in isolation.
if "sounddevice" not in sys.modules:
    sys.modules["sounddevice"] = types.SimpleNamespace(InputStream=object)

from audio.microphone import _supported_input_sample_rate


class FakeSoundDevice:
    def __init__(self, *, supported_rates: set[int], default_rate: int = 48000):
        self.supported_rates = set(supported_rates)
        self.default_rate = default_rate
        self.checked: list[tuple[object, int, int, str]] = []

    def check_input_settings(self, *, device, channels, samplerate, dtype):
        rate = int(samplerate)
        self.checked.append((device, int(channels), rate, str(dtype)))
        if rate not in self.supported_rates:
            raise RuntimeError("Invalid sample rate")

    def query_devices(self, device, kind=None):
        if kind != "input":
            raise AssertionError("Expected input-device query")
        return {"default_samplerate": float(self.default_rate)}


class MicrophoneSampleRateTests(unittest.TestCase):
    def test_keeps_requested_rate_when_supported(self) -> None:
        fake = FakeSoundDevice(supported_rates={16000, 48000})

        result = _supported_input_sample_rate(
            21,
            requested_rate=16000,
            sd_module=fake,
        )

        self.assertEqual(result, 16000)
        self.assertEqual(fake.checked, [(21, 1, 16000, "float32")])

    def test_falls_back_to_device_default_rate(self) -> None:
        fake = FakeSoundDevice(supported_rates={48000}, default_rate=48000)

        result = _supported_input_sample_rate(
            21,
            requested_rate=16000,
            sd_module=fake,
        )

        self.assertEqual(result, 48000)
        self.assertEqual(
            fake.checked,
            [
                (21, 1, 16000, "float32"),
                (21, 1, 48000, "float32"),
            ],
        )

    def test_reports_when_requested_and_default_rates_fail(self) -> None:
        fake = FakeSoundDevice(supported_rates=set(), default_rate=48000)

        with self.assertRaisesRegex(RuntimeError, "supports neither"):
            _supported_input_sample_rate(
                21,
                requested_rate=16000,
                sd_module=fake,
            )


if __name__ == "__main__":
    unittest.main()
