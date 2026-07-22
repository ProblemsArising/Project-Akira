from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.window_transparency import apply_windows_avatar_transparency


class FakeColorFactory:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, alpha, red, green, blue):
        value = (alpha, red, green, blue)
        self.calls.append(value)
        return value


class FakeWindow:
    def __init__(self) -> None:
        controller = SimpleNamespace(DefaultBackgroundColor="opaque")
        control = SimpleNamespace(
            DefaultBackgroundColor="opaque",
            CoreWebView2Controller=controller,
        )
        self.native = SimpleNamespace(
            AllowTransparency=False,
            BackColor=None,
            TransparencyKey=None,
            webview=control,
        )


class WindowTransparencyTests(unittest.TestCase):
    def test_windows_native_form_and_webview_background_are_configured(self):
        window = FakeWindow()
        colors = FakeColorFactory()

        configured = apply_windows_avatar_transparency(
            window,
            platform_name="nt",
            color_factory=colors,
        )

        self.assertTrue(configured)
        self.assertTrue(window.native.AllowTransparency)
        self.assertEqual(window.native.BackColor, (255, 1, 0, 1))
        self.assertEqual(window.native.TransparencyKey, (255, 1, 0, 1))
        self.assertEqual(window.native.webview.DefaultBackgroundColor, "opaque")
        self.assertEqual(
            window.native.webview.CoreWebView2Controller.DefaultBackgroundColor,
            "opaque",
        )

    def test_non_windows_platform_uses_pywebview_transparency_only(self):
        window = FakeWindow()

        self.assertFalse(
            apply_windows_avatar_transparency(window, platform_name="posix")
        )
        self.assertFalse(window.native.AllowTransparency)




if __name__ == "__main__":
    unittest.main()
