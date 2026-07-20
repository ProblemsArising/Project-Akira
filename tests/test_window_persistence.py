from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.desktop import DesktopApplication, build_parser


class EventHook:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        for handler in list(self.handlers):
            handler(*args)


class FakeWindow:
    def __init__(self, kwargs):
        self.x = kwargs.get("x")
        self.y = kwargs.get("y")
        self.width = kwargs["width"]
        self.height = kwargs["height"]
        self.events = SimpleNamespace(
            moved=EventHook(),
            resized=EventHook(),
            maximized=EventHook(),
            restored=EventHook(),
            closing=EventHook(),
            closed=EventHook(),
        )


class FakeWebview:
    def __init__(self) -> None:
        self.settings = {}
        self.windows = []
        self.start_calls = []

    def create_window(self, *args, **kwargs):
        window = FakeWindow(kwargs)
        self.windows.append((args, kwargs, window))
        return window

    def start(self, **kwargs):
        self.start_calls.append(kwargs)


class RecordingBackend:
    url = "http://127.0.0.1:54321"

    def start(self):
        return self.url

    def stop(self, timeout=8.0):
        return None


def settings(*, remember=True, avatar=True, **geometry):
    defaults = {
        "open_avatar_window": avatar,
        "avatar_always_on_top": False,
        "remember_window_positions": remember,
        "main_window_x": None,
        "main_window_y": None,
        "main_window_width": None,
        "main_window_height": None,
        "main_window_maximized": False,
        "avatar_window_x": None,
        "avatar_window_y": None,
        "avatar_window_width": None,
        "avatar_window_height": None,
        "avatar_window_maximized": False,
    }
    defaults.update(geometry)
    return SimpleNamespace(general=SimpleNamespace(**defaults))


class WindowPersistenceTests(unittest.TestCase):
    def run_app(self, saved, **overrides):
        webview = FakeWebview()
        updates = []

        with tempfile.TemporaryDirectory() as directory:
            app = DesktopApplication(
                backend=RecordingBackend(),
                webview_module=webview,
                storage_path=Path(directory),
                settings_loader=lambda: saved,
                settings_updater=lambda changes: updates.append(changes),
                **overrides,
            )
            self.assertEqual(app.run(), 0)

        return webview, updates

    def test_saved_main_and_avatar_geometry_are_restored(self):
        saved = settings(
            main_window_x=120,
            main_window_y=80,
            main_window_width=1360,
            main_window_height=860,
            main_window_maximized=True,
            avatar_window_x=1500,
            avatar_window_y=60,
            avatar_window_width=480,
            avatar_window_height=760,
        )

        webview, _ = self.run_app(saved)

        _, main_kwargs, _ = webview.windows[0]
        _, avatar_kwargs, _ = webview.windows[1]

        self.assertEqual(
            (
                main_kwargs["x"],
                main_kwargs["y"],
                main_kwargs["width"],
                main_kwargs["height"],
            ),
            (120, 80, 1360, 860),
        )
        self.assertTrue(main_kwargs["maximized"])
        self.assertEqual(
            (
                avatar_kwargs["x"],
                avatar_kwargs["y"],
                avatar_kwargs["width"],
                avatar_kwargs["height"],
            ),
            (1500, 60, 480, 760),
        )

    def test_moved_and_resized_bounds_are_saved_on_close(self):
        webview, updates = self.run_app(settings(avatar=False))
        window = webview.windows[0][2]

        window.x = -900
        window.y = 100
        window.width = 1440
        window.height = 900
        window.events.moved.fire()
        window.events.resized.fire(1440, 900)
        window.events.closing.fire()

        self.assertEqual(len(updates), 1)
        general = updates[0]["general"]
        self.assertEqual(general["main_window_x"], -900)
        self.assertEqual(general["main_window_y"], 100)
        self.assertEqual(general["main_window_width"], 1440)
        self.assertEqual(general["main_window_height"], 900)
        self.assertFalse(general["main_window_maximized"])

    def test_maximizing_preserves_last_restored_bounds(self):
        webview, updates = self.run_app(
            settings(
                avatar=False,
                main_window_x=50,
                main_window_y=60,
                main_window_width=1200,
                main_window_height=800,
            )
        )
        window = webview.windows[0][2]

        window.events.maximized.fire()
        window.x = 0
        window.y = 0
        window.width = 2560
        window.height = 1440
        window.events.resized.fire(2560, 1440)
        window.events.closing.fire()

        general = updates[0]["general"]
        self.assertEqual(general["main_window_x"], 50)
        self.assertEqual(general["main_window_y"], 60)
        self.assertEqual(general["main_window_width"], 1200)
        self.assertEqual(general["main_window_height"], 800)
        self.assertTrue(general["main_window_maximized"])

    def test_disabling_persistence_uses_defaults_and_writes_nothing(self):
        webview, updates = self.run_app(
            settings(
                remember=False,
                avatar=False,
                main_window_x=900,
                main_window_width=1700,
            )
        )
        _, kwargs, window = webview.windows[0]

        self.assertIsNone(kwargs["x"])
        self.assertEqual(kwargs["width"], 1180)
        window.events.closing.fire()
        self.assertEqual(updates, [])

    def test_cli_geometry_overrides_saved_values(self):
        webview, _ = self.run_app(
            settings(
                avatar=False,
                main_window_x=10,
                main_window_y=20,
                main_window_width=1000,
                main_window_height=700,
                main_window_maximized=True,
            ),
            x=300,
            y=200,
            width=1500,
            height=950,
            maximized=False,
        )
        _, kwargs, _ = webview.windows[0]

        self.assertEqual(
            (
                kwargs["x"],
                kwargs["y"],
                kwargs["width"],
                kwargs["height"],
            ),
            (300, 200, 1500, 950),
        )
        self.assertFalse(kwargs["maximized"])

    def test_cli_defaults_defer_to_saved_geometry(self):
        arguments = build_parser().parse_args([])
        self.assertIsNone(arguments.width)
        self.assertIsNone(arguments.height)
        self.assertIsNone(arguments.x)
        self.assertIsNone(arguments.y)
        self.assertIsNone(arguments.maximized)
        self.assertIsNone(arguments.avatar_maximized)


if __name__ == "__main__":
    unittest.main()
