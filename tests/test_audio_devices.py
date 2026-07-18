from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from audio.devices import (
    AudioDeviceError,
    canonical_device_selector,
    configure_audio_devices,
    format_audio_device_table,
    input_devices,
    list_audio_devices,
    output_devices,
    resolve_audio_device,
)
from config.settings import get_settings


class FakeSoundDevice:
    def __init__(self) -> None:
        self.default = types.SimpleNamespace(device=(0, 2))
        self._devices = [
            {
                "name": "Microphone Array",
                "hostapi": 0,
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            },
            {
                "name": "Headset",
                "hostapi": 1,
                "max_input_channels": 1,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
            {
                "name": "Speakers",
                "hostapi": 1,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
            {
                "name": "CABLE Input",
                "hostapi": 1,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 44100.0,
            },
        ]
        self._hostapis = [{"name": "MME"}, {"name": "Windows WASAPI"}]

    def query_devices(self):
        return self._devices

    def query_hostapis(self):
        return self._hostapis


class AudioDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.settings_file = Path(self.temp_directory.name) / "settings.json"
        self.environment = mock.patch.dict(
            os.environ,
            {"AKIRA_SETTINGS_FILE": str(self.settings_file)},
        )
        self.environment.start()
        get_settings(reload=True)
        self.devices = list_audio_devices(sd_module=FakeSoundDevice())

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_lists_capabilities_and_defaults(self) -> None:
        self.assertEqual(len(self.devices), 4)
        self.assertTrue(self.devices[0].is_default_input)
        self.assertTrue(self.devices[2].is_default_output)
        self.assertEqual([item.index for item in input_devices(devices=self.devices)], [0, 1])
        self.assertEqual([item.index for item in output_devices(devices=self.devices)], [1, 2, 3])

    def test_resolves_index_exact_name_and_unique_fragment(self) -> None:
        self.assertEqual(resolve_audio_device(0, "input", devices=self.devices).name, "Microphone Array")
        self.assertEqual(resolve_audio_device("Speakers", "output", devices=self.devices).index, 2)
        self.assertEqual(resolve_audio_device("cable", "output", devices=self.devices).index, 3)
        self.assertIsNone(resolve_audio_device("default", "output", devices=self.devices))

    def test_rejects_wrong_capability(self) -> None:
        with self.assertRaises(AudioDeviceError):
            resolve_audio_device(2, "input", devices=self.devices)

    def test_rejects_ambiguous_name(self) -> None:
        with self.assertRaisesRegex(AudioDeviceError, "ambiguous"):
            resolve_audio_device("Windows", "output", devices=self.devices)

    def test_canonical_selector_includes_host_api(self) -> None:
        value = canonical_device_selector(3, "output", devices=self.devices)
        self.assertEqual(value, "CABLE Input | Windows WASAPI")

    def test_configure_persists_validated_devices(self) -> None:
        updated = configure_audio_devices(
            input_device=0,
            output_device="cable",
            devices=self.devices,
        )

        self.assertEqual(updated.audio.input_device, "Microphone Array | MME")
        self.assertEqual(updated.audio.output_device, "CABLE Input | Windows WASAPI")
        reloaded = get_settings(reload=True)
        self.assertEqual(reloaded.audio.output_device, "CABLE Input | Windows WASAPI")

    def test_default_clears_explicit_selection(self) -> None:
        configure_audio_devices(output_device=3, devices=self.devices)
        updated = configure_audio_devices(output_device="default", devices=self.devices)
        self.assertIsNone(updated.audio.output_device)

    def test_device_table_shows_configured_markers(self) -> None:
        configure_audio_devices(input_device=0, output_device=3, devices=self.devices)
        table = format_audio_device_table(devices=self.devices)
        self.assertIn("[I]", table)
        self.assertIn("[O]", table)
        self.assertIn("CABLE Input", table)


class MicrophoneDeviceTests(unittest.TestCase):
    def test_recorder_passes_selected_device(self) -> None:
        # Import normally and mock the recorder function itself. Replacing entries
        # in sys.modules while importing NumPy/SciPy can remove their newly loaded
        # submodules when the patch exits, making later imports unsafe.
        import audio.microphone as microphone

        recorder = microphone.MicrophoneRecorder(input_device=7)
        with mock.patch.object(microphone, "record_audio", return_value="input.wav") as record:
            result = recorder.record()

        self.assertEqual(result, "input.wav")
        record.assert_called_once()
        args, kwargs = record.call_args
        self.assertEqual(args, ("input.wav",))
        self.assertEqual(kwargs["input_device"], 7)
        self.assertEqual(kwargs["sample_rate"], 16000)
        self.assertEqual(kwargs["frame_ms"], 30)
        self.assertIsNotNone(kwargs["stop_event"])


class TTSOutputDeviceTests(unittest.TestCase):
    def _load_tts_module(self):
        # Patch the already imported module attributes instead of re-importing the
        # whole NumPy/SciPy stack inside a temporary sys.modules dictionary.
        import audio.tts as tts_module

        fake_sd = types.ModuleType("sounddevice")
        fake_sd.play = mock.Mock()
        fake_sd.query_devices = mock.Mock()
        fake_sd.check_output_settings = mock.Mock()

        self._sd_patcher = mock.patch.object(tts_module, "sd", fake_sd)
        self._start_patcher = mock.patch.object(tts_module, "start_talking", mock.Mock())
        self._stop_patcher = mock.patch.object(tts_module, "stop_talking", mock.Mock())
        self._sd_patcher.start()
        self._start_patcher.start()
        self._stop_patcher.start()
        self.addCleanup(self._stop_patcher.stop)
        self.addCleanup(self._start_patcher.stop)
        self.addCleanup(self._sd_patcher.stop)
        return tts_module

    def test_selected_output_uses_sounddevice_playback(self) -> None:
        import numpy as np

        tts_module = self._load_tts_module()
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / "speech.wav"
            wav_path.write_bytes(b"fake")

            with mock.patch.object(
                tts_module,
                "_synthesize_to_wav",
                return_value=wav_path,
            ), mock.patch.object(
                tts_module.wav,
                "read",
                return_value=(22050, np.zeros(100, dtype=np.int16)),
            ), mock.patch.object(
                tts_module,
                "_prepare_output_audio",
                return_value=(np.zeros(100, dtype=np.float32), 48000),
            ), mock.patch.object(
                tts_module.time,
                "sleep",
            ):
                tts_module.TextToSpeech(output_device=9).speak("Hello")

        tts_module.sd.play.assert_called_once()
        self.assertEqual(tts_module.sd.play.call_args.kwargs["device"], 9)
        self.assertTrue(tts_module.sd.play.call_args.kwargs["blocking"])
        tts_module.start_talking.assert_called_once_with("Hello")
        tts_module.stop_talking.assert_called_once_with()

    def test_default_output_preserves_direct_pyttsx3_path(self) -> None:
        tts_module = self._load_tts_module()
        engine = mock.Mock()
        engine.getProperty.return_value = [types.SimpleNamespace(id="voice-0")]

        with mock.patch.object(tts_module.pyttsx3, "init", return_value=engine), mock.patch.object(
            tts_module.time,
            "sleep",
        ):
            tts_module.TextToSpeech(output_device=None, voice_index=0).speak("Hello")

        engine.say.assert_called_once_with("Hello")
        engine.runAndWait.assert_called_once_with()
        tts_module.sd.play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
