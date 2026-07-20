from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.desktop import (
    BackendServer,
    DesktopApplication,
    DesktopLaunchError,
    build_parser,
    find_available_port,
)


class FakeUvicornServer:
    def __init__(self) -> None:
        self.should_exit = False
        self.started = False
        self.run_started = threading.Event()
        self.run_stopped = threading.Event()

    def run(self) -> None:
        self.started = True
        self.run_started.set()
        while not self.should_exit:
            self.run_stopped.wait(0.005)
        self.run_stopped.set()


class FakeWebview:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.settings: dict[str, object] = {}
        self.windows: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.start_calls: list[dict[str, object]] = []
        self.fail_start = fail_start

    def create_window(self, *args, **kwargs):
        self.windows.append((args, kwargs))
        return SimpleNamespace()

    def start(self, **kwargs):
        self.start_calls.append(kwargs)
        if self.fail_start:
            raise RuntimeError("GUI failed")


class RecordingBackend:
    def __init__(self) -> None:
        self.url = "http://127.0.0.1:54321"
        self.started = 0
        self.stopped = 0

    def start(self) -> str:
        self.started += 1
        return self.url

    def stop(self, timeout: float = 8.0) -> None:
        self.stopped += 1


class DesktopLauncherTests(unittest.TestCase):
    def test_available_port_is_positive_and_bindable(self) -> None:
        port = find_available_port()
        self.assertGreater(port, 0)
        self.assertLessEqual(port, 65535)

    def test_backend_starts_once_and_stops_thread(self) -> None:
        fake = FakeUvicornServer()
        backend = BackendServer(
            port=49123,
            server_factory=lambda _: fake,
            readiness_probe=lambda url, timeout: fake.run_started.wait(1.0),
        )

        first = backend.start()
        second = backend.start()

        self.assertEqual(first, "http://127.0.0.1:49123")
        self.assertEqual(second, first)
        self.assertTrue(backend.is_running)

        backend.stop()
        self.assertTrue(fake.should_exit)
        self.assertFalse(backend.is_running)

    def test_backend_failure_is_reported_and_cleaned_up(self) -> None:
        fake = FakeUvicornServer()
        backend = BackendServer(
            port=49124,
            startup_timeout=0.01,
            server_factory=lambda _: fake,
            readiness_probe=lambda url, timeout: False,
        )

        with self.assertRaises(DesktopLaunchError):
            backend.start()

        self.assertTrue(fake.should_exit)
        self.assertFalse(backend.is_running)

    def test_desktop_window_uses_managed_backend(self) -> None:
        backend = RecordingBackend()
        webview = FakeWebview()

        with tempfile.TemporaryDirectory() as directory:
            application = DesktopApplication(
                backend=backend,
                webview_module=webview,
                storage_path=Path(directory) / "profile",
                width=1280,
                height=820,
            )
            result = application.run()

        self.assertEqual(result, 0)
        self.assertEqual(backend.started, 1)
        self.assertEqual(backend.stopped, 1)
        self.assertEqual(webview.windows[0][0][:2], ("Project Akira", backend.url))
        self.assertEqual(webview.windows[0][1]["width"], 1280)
        self.assertFalse(webview.start_calls[0]["private_mode"])
        self.assertTrue(webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"])

    def test_backend_stops_when_gui_raises(self) -> None:
        backend = RecordingBackend()
        webview = FakeWebview(fail_start=True)

        with tempfile.TemporaryDirectory() as directory:
            application = DesktopApplication(
                backend=backend,
                webview_module=webview,
                storage_path=Path(directory),
            )
            with self.assertRaises(RuntimeError):
                application.run()

        self.assertEqual(backend.stopped, 1)

    def test_cli_uses_automatic_port_by_default(self) -> None:
        arguments = build_parser().parse_args([])
        self.assertEqual(arguments.port, 0)
        self.assertFalse(arguments.debug)
        self.assertFalse(arguments.maximized)


if __name__ == "__main__":
    unittest.main()
