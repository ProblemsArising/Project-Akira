"""System-tray support for Project Akira's desktop launcher.

The tray module deliberately knows nothing about pywebview.  It owns only the
native tray icon, its menu, and the small HTTP actions that control the already
running FastAPI backend.  Window show/hide/quit behaviour is supplied through
callbacks by :mod:`app.desktop`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TrayLaunchError(RuntimeError):
    """Raised when the optional tray runtime cannot be initialized."""


class TrayActionError(RuntimeError):
    """Raised when a tray action cannot reach the local Project Akira API."""


class TrayBackendClient:
    """Tiny synchronous client for safe localhost tray actions."""

    def __init__(self, base_url: str, *, timeout: float = 4.0) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout = max(0.1, float(timeout))

    def post(self, path: str) -> dict[str, Any]:
        target = f"{self.base_url}/{str(path).lstrip('/')}"
        request = Request(
            target,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            try:
                detail = error.read().decode("utf-8")
            except OSError:
                detail = ""
            raise TrayActionError(
                f"Project Akira returned HTTP {error.code}. {detail}".strip()
            ) from error
        except (OSError, URLError) as error:
            raise TrayActionError(
                "Project Akira's local service is unavailable."
            ) from error

        if not raw.strip():
            return {}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise TrayActionError(
                "Project Akira returned an invalid response."
            ) from error
        return payload if isinstance(payload, dict) else {}


class TrayController:
    """Create and control Project Akira's system-tray icon."""

    def __init__(
        self,
        *,
        base_url: str,
        show_main: Callable[[], None],
        quit_application: Callable[[], None],
        show_avatar: Callable[[], None] | None = None,
        backend_client: TrayBackendClient | Any | None = None,
        pystray_module: Any | None = None,
        image_builder: Callable[[], Any] | None = None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.show_main = show_main
        self.show_avatar = show_avatar
        self.quit_application = quit_application
        self.backend_client = backend_client or TrayBackendClient(self.base_url)
        self._pystray_module = pystray_module
        self._image_builder = image_builder
        self._icon: Any | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._lock = threading.RLock()

    def _pystray(self) -> Any:
        if self._pystray_module is not None:
            return self._pystray_module

        try:
            import pystray
        except ImportError as error:
            raise TrayLaunchError(
                "System-tray support is not installed. Run "
                "`pip install -r requirements.txt`."
            ) from error
        return pystray

    @staticmethod
    def _default_image() -> Any:
        try:
            from PIL import Image, ImageDraw
        except ImportError as error:
            raise TrayLaunchError(
                "Pillow is required for the Project Akira tray icon. Run "
                "`pip install -r requirements.txt`."
            ) from error

        size = 64
        image = Image.new("RGBA", (size, size), (13, 11, 22, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (5, 5, size - 5, size - 5),
            radius=17,
            fill=(139, 92, 246, 255),
        )
        draw.ellipse((14, 14, 50, 50), fill=(239, 107, 216, 255))
        draw.polygon(
            [(21, 47), (31, 18), (34, 18), (44, 47), (37, 47), (35, 40), (28, 40), (26, 47)],
            fill=(255, 255, 255, 255),
        )
        return image

    def _notify(self, message: str, title: str = "Project Akira") -> None:
        with self._lock:
            icon = self._icon
        if icon is None:
            return
        try:
            icon.notify(str(message), str(title))
        except (AttributeError, NotImplementedError, RuntimeError):
            pass

    def notify(self, message: str, title: str = "Project Akira") -> None:
        """Display a best-effort native notification."""

        self._notify(message, title)

    def _run_backend_action(self, path: str, success: str) -> None:
        try:
            self.backend_client.post(path)
        except TrayActionError as error:
            self._notify(str(error), "Project Akira error")
            return
        self._notify(success)

    def _menu(self, pystray: Any) -> Any:
        item = pystray.MenuItem
        entries: list[Any] = [
            item(
                "Open Project Akira",
                lambda _icon, _item: self.show_main(),
                default=True,
            )
        ]

        if self.show_avatar is not None:
            entries.append(
                item(
                    "Show Avatar",
                    lambda _icon, _item: self.show_avatar(),
                )
            )

        entries.extend(
            [
                pystray.Menu.SEPARATOR,
                item(
                    "Start listening",
                    lambda _icon, _item: self._run_backend_action(
                        "/api/listening/start",
                        "Akira is listening.",
                    ),
                ),
                item(
                    "Stop listening",
                    lambda _icon, _item: self._run_backend_action(
                        "/api/listening/stop",
                        "Akira stopped listening.",
                    ),
                ),
                pystray.Menu.SEPARATOR,
                item(
                    "Quit Project Akira",
                    lambda _icon, _item: self.quit_application(),
                ),
            ]
        )
        return pystray.Menu(*entries)

    def start(self) -> None:
        """Start the tray loop in a managed daemon thread.

        On Windows, pystray documents that ``Icon.run`` may safely run outside
        the main thread. Managing the thread ourselves avoids a startup/shutdown
        race in ``run_detached`` where a very short-lived test can call ``stop``
        before the detached icon loop is fully running.
        """

        with self._lock:
            if self._icon is not None:
                return

            pystray = self._pystray()
            image = (
                self._image_builder()
                if self._image_builder is not None
                else self._default_image()
            )
            icon = pystray.Icon(
                "project-akira",
                image,
                "Project Akira",
                self._menu(pystray),
            )
            self._icon = icon
            self._ready.clear()
            self._stop_requested.clear()

            def setup(running_icon: Any) -> None:
                try:
                    running_icon.visible = True
                finally:
                    self._ready.set()
                if self._stop_requested.is_set():
                    running_icon.stop()

            def run_icon() -> None:
                try:
                    icon.run(setup=setup)
                except Exception as error:
                    self._ready.set()
                    self._notify(
                        f"System tray stopped unexpectedly: {error}",
                        "Project Akira error",
                    )
                finally:
                    self._ready.set()

            thread = threading.Thread(
                target=run_icon,
                name="ProjectAkiraTray",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        # Wait briefly so immediate shutdowns cannot race icon initialization.
        self._ready.wait(2.0)
        if not thread.is_alive() and not self._stop_requested.is_set():
            with self._lock:
                self._icon = None
                self._thread = None
            raise TrayLaunchError("Project Akira could not start the system tray.")

    def stop(self, timeout: float = 3.0) -> None:
        """Stop the tray icon and wait briefly for its loop to finish."""

        self._stop_requested.set()
        with self._lock:
            icon = self._icon
            thread = self._thread
            self._icon = None
            self._thread = None

        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

        if (
            thread is not None
            and thread.is_alive()
            and thread.ident != threading.current_thread().ident
        ):
            thread.join(max(0.0, float(timeout)))



__all__ = [
    "TrayActionError",
    "TrayBackendClient",
    "TrayController",
    "TrayLaunchError",
]
