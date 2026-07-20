from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.desktop import DesktopApplication, build_parser
from app.tray import TrayController


class EventHook:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        return [handler(*args) for handler in list(self.handlers)]


class FakeWindow:
    def __init__(self, kwargs):
        self.x = kwargs.get("x")
        self.y = kwargs.get("y")
        self.width = kwargs["width"]
        self.height = kwargs["height"]
        self.hidden = False
        self.shown = False
        self.restored = False
        self.destroyed = False
        self.events = SimpleNamespace(
            moved=EventHook(),
            resized=EventHook(),
            maximized=EventHook(),
            restored=EventHook(),
            closing=EventHook(),
            closed=EventHook(),
        )

    def hide(self):
        self.hidden = True

    def show(self):
        self.shown = True
        self.hidden = False

    def restore(self):
        self.restored = True

    def destroy(self):
        self.destroyed = True
        results = self.events.closing.fire()
        if False not in results:
            self.events.closed.fire()


class ClosedNativeWindow:
    def __init__(self):
        self._x = 40
        self._y = 50
        self._width = 1200
        self._height = 800
        self.destroyed = False
        self.events = SimpleNamespace(
            moved=EventHook(),
            resized=EventHook(),
            maximized=EventHook(),
            restored=EventHook(),
            closing=EventHook(),
            closed=EventHook(),
        )

    def _value(self, value):
        if self.destroyed:
            raise TypeError("native window no longer exists")
        return value

    @property
    def x(self):
        return self._value(self._x)

    @property
    def y(self):
        return self._value(self._y)

    @property
    def width(self):
        return self._value(self._width)

    @property
    def height(self):
        return self._value(self._height)


class FakeWebview:
    def __init__(self, on_start=None) -> None:
        self.settings = {}
        self.windows = []
        self.on_start = on_start

    def create_window(self, *args, **kwargs):
        window = FakeWindow(kwargs)
        self.windows.append((args, kwargs, window))
        return window

    def start(self, **kwargs):
        if self.on_start:
            self.on_start(self)


class FakeTray:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.started = 0
        self.stopped = 0
        self.notifications = []

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def notify(self, message, title="Project Akira"):
        self.notifications.append((message, title))


class RecordingBackend:
    url = "http://127.0.0.1:54321"

    def __init__(self):
        self.stopped = 0

    def start(self):
        return self.url

    def stop(self, timeout=8.0):
        self.stopped += 1


def settings(*, tray=True, close_to_tray=True, avatar=False):
    return SimpleNamespace(
        general=SimpleNamespace(
            system_tray_enabled=tray,
            close_to_tray=close_to_tray,
            open_avatar_window=avatar,
            avatar_always_on_top=False,
            remember_window_positions=False,
        )
    )


class FakeMenuItem:
    def __init__(self, text, action, default=False, enabled=True):
        self.text = text
        self.action = action
        self.default = default
        self.enabled = enabled


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = list(items)


class FakeIcon:
    def __init__(self, name, image, title, menu):
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu
        self.started = 0
        self.stopped = 0
        self.notifications = []
        self.visible = False
        self._stopped = __import__("threading").Event()

    def run(self, setup=None):
        self.started += 1
        if setup is not None:
            setup(self)
        self._stopped.wait(2.0)

    def stop(self):
        self.stopped += 1
        self._stopped.set()

    def notify(self, message, title):
        self.notifications.append((message, title))


class FakePystray:
    Menu = FakeMenu
    MenuItem = FakeMenuItem
    Icon = FakeIcon


class FakeBackendClient:
    def __init__(self):
        self.paths = []

    def post(self, path):
        self.paths.append(path)
        return {"ok": True}


class TrayControllerTests(unittest.TestCase):
    def test_menu_actions_open_windows_control_listening_and_quit(self):
        calls = []
        client = FakeBackendClient()
        controller = TrayController(
            base_url="http://127.0.0.1:8000",
            show_main=lambda: calls.append("main"),
            show_avatar=lambda: calls.append("avatar"),
            quit_application=lambda: calls.append("quit"),
            backend_client=client,
            pystray_module=FakePystray,
            image_builder=lambda: object(),
        )

        controller.start()
        icon = controller._icon
        self.assertEqual(icon.started, 1)

        items = {
            item.text: item
            for item in icon.menu.items
            if isinstance(item, FakeMenuItem)
        }
        self.assertTrue(items["Open Project Akira"].default)
        items["Open Project Akira"].action(icon, items["Open Project Akira"])
        items["Show Avatar"].action(icon, items["Show Avatar"])
        items["Start listening"].action(icon, items["Start listening"])
        items["Stop listening"].action(icon, items["Stop listening"])
        items["Quit Project Akira"].action(icon, items["Quit Project Akira"])

        self.assertEqual(calls, ["main", "avatar", "quit"])
        self.assertEqual(
            client.paths,
            ["/api/listening/start", "/api/listening/stop"],
        )

        controller.stop()
        self.assertEqual(icon.stopped, 1)


class DesktopTrayTests(unittest.TestCase):
    def run_app(self, saved, *, on_start=None, **overrides):
        webview = FakeWebview(on_start=on_start)
        backend = RecordingBackend()
        trays = []

        def tray_factory(**callbacks):
            tray = FakeTray(**callbacks)
            trays.append(tray)
            return tray

        with tempfile.TemporaryDirectory() as directory:
            result = DesktopApplication(
                backend=backend,
                webview_module=webview,
                storage_path=Path(directory),
                settings_loader=lambda: saved,
                tray_factory=tray_factory,
                **overrides,
            ).run()

        self.assertEqual(result, 0)
        return webview, backend, trays

    def test_close_button_hides_main_window_and_notifies(self):
        captured = {}

        def on_start(webview):
            main = webview.windows[0][2]
            captured["results"] = main.events.closing.fire()
            captured["main"] = main

        _, backend, trays = self.run_app(
            settings(tray=True, close_to_tray=True),
            on_start=on_start,
        )

        main = captured["main"]
        self.assertTrue(main.hidden)
        self.assertIn(False, captured["results"])
        self.assertEqual(trays[0].started, 1)
        self.assertEqual(len(trays[0].notifications), 1)
        self.assertGreaterEqual(trays[0].stopped, 1)
        self.assertEqual(backend.stopped, 1)

    def test_tray_callbacks_reopen_windows_and_quit(self):
        captured = {}

        def on_start(webview):
            captured["main"] = webview.windows[0][2]
            tray = captured["tray"]
            tray.callbacks["show_main"]()
            tray.callbacks["quit_application"]()

        webview = FakeWebview(on_start=on_start)
        backend = RecordingBackend()

        def tray_factory(**callbacks):
            tray = FakeTray(**callbacks)
            captured["tray"] = tray
            return tray

        with tempfile.TemporaryDirectory() as directory:
            DesktopApplication(
                backend=backend,
                webview_module=webview,
                storage_path=Path(directory),
                settings_loader=lambda: settings(tray=True),
                tray_factory=tray_factory,
            ).run()

        main = captured["main"]
        self.assertTrue(main.shown)
        self.assertTrue(main.restored)
        self.assertTrue(main.destroyed)

    def test_exit_on_close_does_not_hide_window(self):
        captured = {}

        def on_start(webview):
            main = webview.windows[0][2]
            captured["results"] = main.events.closing.fire()
            captured["main"] = main

        self.run_app(
            settings(tray=True, close_to_tray=False),
            on_start=on_start,
        )

        self.assertFalse(captured["main"].hidden)
        self.assertNotIn(False, captured["results"])

    def test_missing_tray_setting_keeps_old_test_fixtures_compatible(self):
        saved = SimpleNamespace(
            general=SimpleNamespace(
                open_avatar_window=False,
                avatar_always_on_top=False,
                remember_window_positions=False,
            )
        )
        _, _, trays = self.run_app(saved)
        self.assertEqual(trays, [])

    def test_closed_window_persists_cached_geometry_without_native_handle(self):
        updates = []
        window = ClosedNativeWindow()
        application = DesktopApplication(
            backend=RecordingBackend(),
            webview_module=FakeWebview(),
            settings_loader=lambda: SimpleNamespace(
                general=SimpleNamespace(
                    remember_window_positions=True,
                    open_avatar_window=False,
                    avatar_always_on_top=False,
                    system_tray_enabled=False,
                    close_to_tray=False,
                )
            ),
            settings_updater=lambda changes: updates.append(changes),
        )
        from app.desktop import WindowGeometry

        application._track_window_geometry(
            window,
            name="main",
            initial=WindowGeometry(width=1200, height=800, x=40, y=50),
            minimum_size=(800, 560),
        )
        window.destroyed = True
        window.events.closed.fire()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["general"]["main_window_x"], 40)
        self.assertEqual(updates[0]["general"]["main_window_width"], 1200)

    def test_injected_fake_webview_never_starts_real_tray_implicitly(self):
        webview = FakeWebview()
        application = DesktopApplication(
            backend=RecordingBackend(),
            webview_module=webview,
            settings_loader=lambda: settings(tray=True, close_to_tray=True),
        )
        application._tray_factory = lambda **kwargs: self.fail(
            "A native tray must not start from an injected fake webview"
        )

        self.assertEqual(application.run(), 0)

    def test_cli_defaults_and_overrides(self):
        defaults = build_parser().parse_args([])
        self.assertIsNone(defaults.system_tray_enabled)
        self.assertIsNone(defaults.close_to_tray)

        disabled = build_parser().parse_args(["--no-tray", "--exit-on-close"])
        self.assertFalse(disabled.system_tray_enabled)
        self.assertFalse(disabled.close_to_tray)

        enabled = build_parser().parse_args(["--tray", "--close-to-tray"])
        self.assertTrue(enabled.system_tray_enabled)
        self.assertTrue(enabled.close_to_tray)


if __name__ == "__main__":
    unittest.main()
