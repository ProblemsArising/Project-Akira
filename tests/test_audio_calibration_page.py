from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.api import BackendRuntime, create_app
from app.history import ChatHistoryStore
from audio.devices import AudioDevice
from config.settings import get_settings


@dataclass
class FakeCalibrationResult:
    noise_floor: float = 0.001
    average_level: float = 0.03
    peak_level: float = 0.12
    current_start_threshold: float = 0.02
    current_end_threshold: float = 0.006
    suggested_min_start_threshold: float = 0.006
    suggested_min_end_threshold: float = 0.0018
    sample_rate: int = 48000
    duration_seconds: float = 2.0
    calibration_seconds: float = 0.5
    sample_file: str = "data/calibration_sample.wav"

    def to_dict(self):
        return dict(self.__dict__)


class FakeCalibrationSession:
    def __init__(self, input_device=None):
        self.input_device = input_device
        self.stopped = False

    def run(self, *, duration_seconds, calibration_seconds, on_level):
        on_level(
            {
                "phase": "noise",
                "rms": 0.001,
                "peak": 0.002,
                "dbfs": -60.0,
                "progress": 0.25,
                "overflowed": False,
            }
        )
        on_level(
            {
                "phase": "speech",
                "rms": 0.08,
                "peak": 0.12,
                "dbfs": -21.9,
                "progress": 1.0,
                "overflowed": False,
            }
        )
        return FakeCalibrationResult(
            duration_seconds=duration_seconds,
            calibration_seconds=calibration_seconds,
        )

    def request_stop(self):
        self.stopped = True


class AudioCalibrationPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        root = Path(self.temp_directory.name)
        self.settings_file = root / "settings.json"
        self.history_file = root / "history.db"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "AKIRA_SETTINGS_FILE": str(self.settings_file),
                "AKIRA_HISTORY_FILE": str(self.history_file),
            },
        )
        self.environment.start()
        get_settings(reload=True)
        self.service_factory_calls = 0
        self.calibration_sessions = []

        def service_factory():
            self.service_factory_calls += 1
            raise AssertionError("Audio calibration must not load the conversation service")

        def calibration_factory(**kwargs):
            session = FakeCalibrationSession(**kwargs)
            self.calibration_sessions.append(session)
            return session

        self.runtime = BackendRuntime(
            service_factory=service_factory,
            history_factory=lambda: ChatHistoryStore(self.history_file),
            calibration_factory=calibration_factory,
        )
        self.client_context = TestClient(create_app(self.runtime))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.environment.stop()
        self.temp_directory.cleanup()

    def test_page_and_assets_do_not_load_conversation_service(self) -> None:
        page = self.client.get("/audio")
        stylesheet = self.client.get("/static/audio/styles.css")
        script = self.client.get("/static/audio/app.js")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Audio calibration", page.text)
        self.assertIn("/static/audio/app.js", page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(".meter-track", stylesheet.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("/api/audio/calibration", script.text)
        self.assertEqual(self.service_factory_calls, 0)

    def test_device_endpoint_returns_microphones_without_loading_service(self) -> None:
        devices = [
            AudioDevice(
                index=4,
                name="Test microphone",
                host_api="Windows WASAPI",
                max_input_channels=1,
                max_output_channels=0,
                default_sample_rate=48000.0,
                is_default_input=True,
            )
        ]
        with mock.patch("audio.devices.list_audio_devices", return_value=devices):
            response = self.client.get("/api/audio/devices")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["devices"][0]["name"], "Test microphone")
        self.assertEqual(self.service_factory_calls, 0)

    def test_calibration_websocket_streams_levels_and_result(self) -> None:
        with self.client.websocket_connect("/api/audio/calibration") as websocket:
            ready = websocket.receive_json()
            self.assertEqual(ready["type"], "calibration.ready")

            websocket.send_json(
                {
                    "type": "start",
                    "input_device": None,
                    "duration_seconds": 2.0,
                    "calibration_seconds": 0.5,
                }
            )
            started = websocket.receive_json()
            self.assertEqual(started["type"], "calibration.started")

            messages = [websocket.receive_json() for _ in range(3)]
            self.assertEqual(messages[0]["type"], "calibration.level")
            self.assertEqual(messages[1]["type"], "calibration.level")
            self.assertEqual(messages[2]["type"], "calibration.completed")
            self.assertAlmostEqual(messages[2]["data"]["noise_floor"], 0.001)
            self.assertIn("sample_url", messages[2]["data"])

        self.assertEqual(len(self.calibration_sessions), 1)
        self.assertEqual(self.service_factory_calls, 0)


if __name__ == "__main__":
    unittest.main()
