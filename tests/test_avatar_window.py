from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from app.desktop import DesktopApplication, build_parser


class EventHook:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(
            before_show=EventHook(),
            moved=EventHook(),
            resized=EventHook(),
            maximized=EventHook(),
            restored=EventHook(),
            closing=EventHook(),
            closed=EventHook(),
        )


class FakeWebview:
    def __init__(self) -> None:
        self.settings: dict[str, object] = {}
        self.windows: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.start_calls: list[dict[str, object]] = []
        self.created_windows: list[FakeWindow] = []

    def create_window(self, *args, **kwargs):
        self.windows.append((args, kwargs))
        window = FakeWindow()
        self.created_windows.append(window)
        return window

    def start(self, **kwargs):
        self.start_calls.append(kwargs)


class RecordingBackend:
    url = "http://127.0.0.1:54321"

    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> str:
        self.started += 1
        return self.url

    def stop(self, timeout: float = 8.0) -> None:
        self.stopped += 1


def settings(
    *,
    open_avatar: bool = True,
    on_top: bool = False,
    transparent: bool = False,
):
    return SimpleNamespace(
        general=SimpleNamespace(
            open_avatar_window=open_avatar,
            avatar_always_on_top=on_top,
            avatar_transparent_window=transparent,
        )
    )


class AvatarWindowTests(unittest.TestCase):
    def test_desktop_creates_separate_avatar_window_from_settings(self) -> None:
        backend = RecordingBackend()
        webview = FakeWebview()

        with tempfile.TemporaryDirectory() as directory:
            application = DesktopApplication(
                backend=backend,
                webview_module=webview,
                storage_path=Path(directory),
                settings_loader=lambda: settings(open_avatar=True, on_top=True),
            )
            self.assertEqual(application.run(), 0)

        self.assertEqual(len(webview.windows), 2)
        avatar_args, avatar_kwargs = webview.windows[1]
        self.assertEqual(avatar_args[:2], ("Akira", f"{backend.url}/avatar"))
        self.assertTrue(avatar_kwargs["on_top"])
        self.assertFalse(avatar_kwargs["transparent"])
        self.assertFalse(avatar_kwargs["hidden"])
        self.assertFalse(avatar_kwargs["text_select"])
        self.assertEqual(backend.stopped, 1)

    def test_saved_setting_can_disable_avatar_window(self) -> None:
        webview = FakeWebview()

        with tempfile.TemporaryDirectory() as directory:
            DesktopApplication(
                backend=RecordingBackend(),
                webview_module=webview,
                storage_path=Path(directory),
                settings_loader=lambda: settings(open_avatar=False),
            ).run()

        self.assertEqual(len(webview.windows), 1)

    def test_cli_override_wins_over_saved_setting(self) -> None:
        webview = FakeWebview()

        with tempfile.TemporaryDirectory() as directory:
            DesktopApplication(
                backend=RecordingBackend(),
                webview_module=webview,
                storage_path=Path(directory),
                settings_loader=lambda: settings(open_avatar=False, on_top=False),
                open_avatar_window=True,
                avatar_always_on_top=True,
                avatar_width=500,
                avatar_height=800,
            ).run()

        self.assertEqual(len(webview.windows), 2)
        _, avatar_kwargs = webview.windows[1]
        self.assertEqual(avatar_kwargs["width"], 500)
        self.assertEqual(avatar_kwargs["height"], 800)
        self.assertTrue(avatar_kwargs["on_top"])

    def test_windows_transparency_uses_webview_alpha_and_native_chroma_key(self) -> None:
        webview = FakeWebview()

        with tempfile.TemporaryDirectory() as directory:
            storage_path = Path(directory)

            with patch("app.desktop.os.name", "nt"):
                DesktopApplication(
                    backend=RecordingBackend(),
                    webview_module=webview,
                    storage_path=storage_path,
                    settings_loader=lambda: settings(transparent=True),
                ).run()

        avatar_args, avatar_kwargs = webview.windows[1]
        avatar_window = webview.created_windows[1]
        self.assertTrue(str(avatar_args[1]).endswith("/avatar?transparent=1"))
        self.assertTrue(avatar_kwargs["transparent"])
        self.assertFalse(avatar_kwargs["hidden"])
        self.assertFalse(avatar_kwargs["resizable"])
        self.assertFalse(avatar_kwargs["maximized"])
        self.assertEqual(avatar_kwargs["background_color"], "#010001")
        self.assertEqual(len(avatar_window.events.before_show.handlers), 1)

    def test_non_windows_transparency_uses_pywebview_support(self) -> None:
        webview = FakeWebview()

        with tempfile.TemporaryDirectory() as directory:
            storage_path = Path(directory)
            with patch("app.desktop.os.name", "posix"):
                DesktopApplication(
                    backend=RecordingBackend(),
                    webview_module=webview,
                    storage_path=storage_path,
                    settings_loader=lambda: settings(transparent=True),
                ).run()

        _, avatar_kwargs = webview.windows[1]
        self.assertTrue(avatar_kwargs["transparent"])
        self.assertFalse(avatar_kwargs["hidden"])
        self.assertEqual(avatar_kwargs["background_color"], "#0d0b16")

    def test_cli_defaults_defer_to_persisted_settings(self) -> None:
        arguments = build_parser().parse_args([])
        self.assertIsNone(arguments.open_avatar_window)
        self.assertIsNone(arguments.avatar_always_on_top)
        self.assertIsNone(arguments.avatar_transparent_window)

        disabled = build_parser().parse_args(["--no-avatar-window"])
        self.assertFalse(disabled.open_avatar_window)

        forced = build_parser().parse_args(
            ["--avatar-window", "--avatar-always-on-top"]
        )
        self.assertTrue(forced.open_avatar_window)
        self.assertTrue(forced.avatar_always_on_top)

        transparent = build_parser().parse_args(["--transparent-avatar-window"])
        self.assertTrue(transparent.avatar_transparent_window)
        opaque = build_parser().parse_args(["--opaque-avatar-window"])
        self.assertFalse(opaque.avatar_transparent_window)


if __name__ == "__main__":
    unittest.main()
