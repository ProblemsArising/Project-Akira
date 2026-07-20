"""Native desktop shell for Project Akira's local WebUI.

The desktop launcher owns a small Uvicorn server thread and displays the
existing FastAPI WebUI inside pywebview.  Heavy AI/audio services remain lazy
inside :mod:`app.api`, exactly as they are when ``server.py`` is used.
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_TITLE = "Project Akira"
DEFAULT_WIDTH = 1180
DEFAULT_HEIGHT = 780
DEFAULT_MIN_SIZE = (800, 560)
DEFAULT_AVATAR_TITLE = "Akira"
DEFAULT_AVATAR_WIDTH = 420
DEFAULT_AVATAR_HEIGHT = 680
DEFAULT_AVATAR_MIN_SIZE = (300, 420)
DEFAULT_STARTUP_TIMEOUT = 20.0


class DesktopLaunchError(RuntimeError):
    """Raised when the local backend or native window cannot be started."""


class ServerLike(Protocol):
    should_exit: bool
    started: bool

    def run(self) -> None: ...


ServerFactory = Callable[[Any], ServerLike]
ReadinessProbe = Callable[[str, float], bool]
SettingsLoader = Callable[[], Any]
SettingsUpdater = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class WindowGeometry:
    """Persistable logical-pixel geometry for one pywebview window."""

    width: int
    height: int
    x: int | None = None
    y: int | None = None
    maximized: bool = False


def find_available_port(host: str = DEFAULT_HOST) -> int:
    """Return an unused TCP port on ``host``.

    The socket is released immediately, so this is not a permanent reservation,
    but it avoids hardcoding port 8000 and lets the desktop shell coexist with a
    developer-run Project Akira server.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind((host, 0))
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(candidate.getsockname()[1])


def default_webview_storage_path() -> Path:
    """Return a writable location for cookies and browser local storage."""

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Project Akira" / "WebView"

    return Path.home() / ".project-akira" / "webview"


def _default_readiness_probe(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout))
    health_url = f"{url.rstrip('/')}/api/health"

    while time.monotonic() < deadline:
        try:
            with urlopen(health_url, timeout=0.5) as response:  # noqa: S310
                if response.status == 200:
                    return True
        except (OSError, URLError):
            time.sleep(0.05)

    return False


class BackendServer:
    """Run Project Akira's FastAPI app in a managed background thread."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = 0,
        log_level: str = "warning",
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        server_factory: ServerFactory | None = None,
        readiness_probe: ReadinessProbe = _default_readiness_probe,
    ) -> None:
        self.host = str(host).strip() or DEFAULT_HOST
        self.port = int(port) if int(port) > 0 else find_available_port(self.host)
        self.log_level = str(log_level)
        self.startup_timeout = float(startup_timeout)
        self._server_factory = server_factory
        self._readiness_probe = readiness_probe
        self._server: ServerLike | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def _create_server(self) -> ServerLike:
        if self._server_factory is not None:
            return self._server_factory(self)

        import uvicorn

        configuration = uvicorn.Config(
            "app.api:app",
            host=self.host,
            port=self.port,
            log_level=self.log_level,
            access_log=False,
            reload=False,
        )
        return uvicorn.Server(configuration)

    def start(self) -> str:
        """Start the backend once and wait until its health route responds."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.url

            self._server = self._create_server()
            self._thread = threading.Thread(
                target=self._server.run,
                name="ProjectAkiraBackend",
                daemon=True,
            )
            self._thread.start()

        if not self._readiness_probe(self.url, self.startup_timeout):
            self.stop()
            raise DesktopLaunchError(
                "Project Akira's local backend did not become ready within "
                f"{self.startup_timeout:g} seconds."
            )

        return self.url

    def stop(self, timeout: float = 8.0) -> None:
        """Request a graceful Uvicorn/FastAPI shutdown and join its thread."""

        with self._lock:
            server = self._server
            thread = self._thread
            if server is not None:
                server.should_exit = True

        if thread is not None and thread.is_alive():
            thread.join(max(0.0, float(timeout)))

        with self._lock:
            self._thread = None
            self._server = None


class DesktopApplication:
    """Coordinate the managed backend and the pywebview GUI loop."""

    def __init__(
        self,
        *,
        backend: BackendServer | None = None,
        title: str = DEFAULT_TITLE,
        width: int | None = None,
        height: int | None = None,
        x: int | None = None,
        y: int | None = None,
        maximized: bool | None = None,
        debug: bool = False,
        storage_path: Path | None = None,
        webview_module: Any | None = None,
        open_avatar_window: bool | None = None,
        avatar_always_on_top: bool | None = None,
        avatar_width: int | None = None,
        avatar_height: int | None = None,
        avatar_x: int | None = None,
        avatar_y: int | None = None,
        avatar_maximized: bool | None = None,
        settings_loader: SettingsLoader | None = None,
        settings_updater: SettingsUpdater | None = None,
    ) -> None:
        self.backend = backend or BackendServer()
        self.title = str(title)
        self.width = None if width is None else max(DEFAULT_MIN_SIZE[0], int(width))
        self.height = None if height is None else max(DEFAULT_MIN_SIZE[1], int(height))
        self.x = None if x is None else int(x)
        self.y = None if y is None else int(y)
        self.maximized = maximized
        self.debug = bool(debug)
        self.storage_path = storage_path or default_webview_storage_path()
        self.open_avatar_window = open_avatar_window
        self.avatar_always_on_top = avatar_always_on_top
        self.avatar_width = (
            None
            if avatar_width is None
            else max(DEFAULT_AVATAR_MIN_SIZE[0], int(avatar_width))
        )
        self.avatar_height = (
            None
            if avatar_height is None
            else max(DEFAULT_AVATAR_MIN_SIZE[1], int(avatar_height))
        )
        self.avatar_x = None if avatar_x is None else int(avatar_x)
        self.avatar_y = None if avatar_y is None else int(avatar_y)
        self.avatar_maximized = avatar_maximized
        self._webview_module = webview_module
        self._settings_loader = settings_loader
        self._settings_updater = settings_updater

    def _general_settings(self) -> Any:
        """Load desktop-window preferences without initializing AI services."""

        if self._settings_loader is not None:
            settings = self._settings_loader()
        else:
            from config.settings import get_settings

            settings = get_settings()
        return settings.general

    def _avatar_window_preferences(self) -> tuple[bool, bool]:
        """Resolve CLI overrides against the persisted desktop settings."""

        general = self._general_settings()
        enabled = (
            bool(self.open_avatar_window)
            if self.open_avatar_window is not None
            else bool(getattr(general, "open_avatar_window", True))
        )
        on_top = (
            bool(self.avatar_always_on_top)
            if self.avatar_always_on_top is not None
            else bool(getattr(general, "avatar_always_on_top", False))
        )
        return enabled, on_top

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _remember_geometry(self) -> bool:
        return bool(
            getattr(self._general_settings(), "remember_window_positions", True)
        )

    def _saved_geometry(
        self,
        name: str,
        *,
        default_width: int,
        default_height: int,
        minimum_size: tuple[int, int],
    ) -> WindowGeometry:
        """Read one window's saved bounds, falling back to safe defaults."""

        general = self._general_settings()
        width = self._optional_int(
            getattr(general, f"{name}_window_width", None)
        )
        height = self._optional_int(
            getattr(general, f"{name}_window_height", None)
        )
        x = self._optional_int(getattr(general, f"{name}_window_x", None))
        y = self._optional_int(getattr(general, f"{name}_window_y", None))

        return WindowGeometry(
            width=max(minimum_size[0], width or default_width),
            height=max(minimum_size[1], height or default_height),
            x=x,
            y=y,
            maximized=bool(
                getattr(general, f"{name}_window_maximized", False)
            ),
        )

    def _resolved_geometry(self, name: str) -> WindowGeometry:
        """Resolve CLI overrides against saved geometry and defaults."""

        if name == "main":
            saved = self._saved_geometry(
                "main",
                default_width=DEFAULT_WIDTH,
                default_height=DEFAULT_HEIGHT,
                minimum_size=DEFAULT_MIN_SIZE,
            )
            width = self.width
            height = self.height
            x = self.x
            y = self.y
            maximized = self.maximized
            minimum = DEFAULT_MIN_SIZE
        elif name == "avatar":
            saved = self._saved_geometry(
                "avatar",
                default_width=DEFAULT_AVATAR_WIDTH,
                default_height=DEFAULT_AVATAR_HEIGHT,
                minimum_size=DEFAULT_AVATAR_MIN_SIZE,
            )
            width = self.avatar_width
            height = self.avatar_height
            x = self.avatar_x
            y = self.avatar_y
            maximized = self.avatar_maximized
            minimum = DEFAULT_AVATAR_MIN_SIZE
        else:
            raise ValueError(f"Unknown desktop window: {name}")

        if not self._remember_geometry():
            saved = WindowGeometry(
                width=DEFAULT_WIDTH if name == "main" else DEFAULT_AVATAR_WIDTH,
                height=DEFAULT_HEIGHT if name == "main" else DEFAULT_AVATAR_HEIGHT,
            )

        return WindowGeometry(
            width=max(minimum[0], width if width is not None else saved.width),
            height=max(minimum[1], height if height is not None else saved.height),
            x=x if x is not None else saved.x,
            y=y if y is not None else saved.y,
            maximized=(
                bool(maximized)
                if maximized is not None
                else saved.maximized
            ),
        )

    def _update_settings(self, changes: Mapping[str, Any]) -> Any:
        if self._settings_updater is not None:
            return self._settings_updater(changes)

        from config.settings import update_settings

        return update_settings(changes)

    def _track_window_geometry(
        self,
        window: Any,
        *,
        name: str,
        initial: WindowGeometry,
        minimum_size: tuple[int, int],
    ) -> None:
        """Track normal bounds and persist them when a window closes.

        pywebview emits move, resize, maximize, restore, and close events.
        Writes are intentionally deferred until close so dragging a window does
        not rewrite settings dozens of times per second.
        """

        if not self._remember_geometry():
            return

        state: dict[str, Any] = {
            "x": initial.x,
            "y": initial.y,
            "width": initial.width,
            "height": initial.height,
            "maximized": initial.maximized,
            "saved": False,
        }

        def update_normal_bounds() -> None:
            if state["maximized"]:
                return

            current_x = self._optional_int(getattr(window, "x", None))
            current_y = self._optional_int(getattr(window, "y", None))
            current_width = self._optional_int(getattr(window, "width", None))
            current_height = self._optional_int(getattr(window, "height", None))

            if current_x is not None:
                state["x"] = current_x
            if current_y is not None:
                state["y"] = current_y
            if current_width is not None:
                state["width"] = max(minimum_size[0], current_width)
            if current_height is not None:
                state["height"] = max(minimum_size[1], current_height)

        def moved(*_args: Any) -> None:
            update_normal_bounds()

        def resized(*args: Any) -> None:
            if state["maximized"]:
                return

            # pywebview may supply width and height to resized handlers.
            numeric = [
                self._optional_int(value)
                for value in args
                if self._optional_int(value) is not None
            ]
            if len(numeric) >= 2:
                state["width"] = max(minimum_size[0], numeric[-2])
                state["height"] = max(minimum_size[1], numeric[-1])
            update_normal_bounds()

        def maximized(*_args: Any) -> None:
            state["maximized"] = True

        def restored(*_args: Any) -> None:
            state["maximized"] = False
            update_normal_bounds()

        def persist(*_args: Any) -> None:
            if state["saved"]:
                return
            state["saved"] = True
            update_normal_bounds()
            self._update_settings(
                {
                    "general": {
                        f"{name}_window_x": state["x"],
                        f"{name}_window_y": state["y"],
                        f"{name}_window_width": state["width"],
                        f"{name}_window_height": state["height"],
                        f"{name}_window_maximized": state["maximized"],
                    }
                }
            )

        events = getattr(window, "events", None)
        if events is None:
            return

        events.moved += moved
        events.resized += resized
        events.maximized += maximized
        events.restored += restored
        events.closing += persist
        events.closed += persist

    def _webview(self) -> Any:
        if self._webview_module is not None:
            return self._webview_module

        try:
            import webview
        except ImportError as error:
            raise DesktopLaunchError(
                "pywebview is not installed. Run `pip install -r requirements.txt`."
            ) from error

        return webview

    def run(self) -> int:
        """Start the backend, open the native window, and clean up on exit."""

        webview = self._webview()
        self.storage_path.mkdir(parents=True, exist_ok=True)

        try:
            url = self.backend.start()
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
            webview.settings["ALLOW_DOWNLOADS"] = False

            main_geometry = self._resolved_geometry("main")
            main_window = webview.create_window(
                self.title,
                url,
                width=main_geometry.width,
                height=main_geometry.height,
                x=main_geometry.x,
                y=main_geometry.y,
                min_size=DEFAULT_MIN_SIZE,
                resizable=True,
                maximized=main_geometry.maximized,
                background_color="#0d0b16",
                text_select=True,
                zoomable=True,
            )
            self._track_window_geometry(
                main_window,
                name="main",
                initial=main_geometry,
                minimum_size=DEFAULT_MIN_SIZE,
            )

            avatar_enabled, avatar_on_top = self._avatar_window_preferences()
            if avatar_enabled:
                avatar_geometry = self._resolved_geometry("avatar")
                avatar_window = webview.create_window(
                    DEFAULT_AVATAR_TITLE,
                    f"{url}/avatar",
                    width=avatar_geometry.width,
                    height=avatar_geometry.height,
                    x=avatar_geometry.x,
                    y=avatar_geometry.y,
                    min_size=DEFAULT_AVATAR_MIN_SIZE,
                    resizable=True,
                    maximized=avatar_geometry.maximized,
                    on_top=avatar_on_top,
                    background_color="#0d0b16",
                    text_select=False,
                    zoomable=True,
                )
                self._track_window_geometry(
                    avatar_window,
                    name="avatar",
                    initial=avatar_geometry,
                    minimum_size=DEFAULT_AVATAR_MIN_SIZE,
                )

            webview.start(
                debug=self.debug,
                private_mode=False,
                storage_path=str(self.storage_path),
            )
            return 0
        finally:
            self.backend.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open Project Akira in a native pywebview desktop window."
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Local backend bind address. Keep 127.0.0.1 for normal use.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Backend port. Zero selects an available port automatically.",
    )
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--x", type=int, default=None)
    parser.add_argument("--y", type=int, default=None)
    main_window_state = parser.add_mutually_exclusive_group()
    main_window_state.add_argument(
        "--maximized",
        dest="maximized",
        action="store_true",
        help="Open the main window maximized.",
    )
    main_window_state.add_argument(
        "--windowed",
        dest="maximized",
        action="store_false",
        help="Open the main window restored even if it was maximized last time.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable pywebview debug mode and developer tools.",
    )
    avatar_visibility = parser.add_mutually_exclusive_group()
    avatar_visibility.add_argument(
        "--avatar-window",
        dest="open_avatar_window",
        action="store_true",
        help="Open the separate avatar stage regardless of saved settings.",
    )
    avatar_visibility.add_argument(
        "--no-avatar-window",
        dest="open_avatar_window",
        action="store_false",
        help="Launch only the main Project Akira window.",
    )
    avatar_layer = parser.add_mutually_exclusive_group()
    avatar_layer.add_argument(
        "--avatar-always-on-top",
        dest="avatar_always_on_top",
        action="store_true",
        help="Keep the avatar stage above other windows.",
    )
    avatar_layer.add_argument(
        "--avatar-normal-z-order",
        dest="avatar_always_on_top",
        action="store_false",
        help="Do not force the avatar stage above other windows.",
    )
    parser.set_defaults(
        maximized=None,
        open_avatar_window=None,
        avatar_always_on_top=None,
        avatar_maximized=None,
    )
    parser.add_argument("--avatar-width", type=int, default=None)
    parser.add_argument("--avatar-height", type=int, default=None)
    parser.add_argument("--avatar-x", type=int, default=None)
    parser.add_argument("--avatar-y", type=int, default=None)
    avatar_window_state = parser.add_mutually_exclusive_group()
    avatar_window_state.add_argument(
        "--avatar-maximized",
        dest="avatar_maximized",
        action="store_true",
        help="Open the avatar window maximized.",
    )
    avatar_window_state.add_argument(
        "--avatar-windowed",
        dest="avatar_maximized",
        action="store_false",
        help="Open the avatar window restored.",
    )
    parser.add_argument(
        "--server-log-level",
        default="warning",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    backend = BackendServer(
        host=arguments.host,
        port=arguments.port,
        log_level=arguments.server_log_level,
    )
    application = DesktopApplication(
        backend=backend,
        width=arguments.width,
        height=arguments.height,
        x=arguments.x,
        y=arguments.y,
        maximized=arguments.maximized,
        debug=arguments.debug,
        open_avatar_window=arguments.open_avatar_window,
        avatar_always_on_top=arguments.avatar_always_on_top,
        avatar_width=arguments.avatar_width,
        avatar_height=arguments.avatar_height,
        avatar_x=arguments.avatar_x,
        avatar_y=arguments.avatar_y,
        avatar_maximized=arguments.avatar_maximized,
    )

    try:
        return application.run()
    except DesktopLaunchError as error:
        print(f"Project Akira could not start: {error}")
        return 1


__all__ = [
    "BackendServer",
    "DEFAULT_AVATAR_HEIGHT",
    "DEFAULT_AVATAR_MIN_SIZE",
    "DEFAULT_AVATAR_TITLE",
    "DEFAULT_AVATAR_WIDTH",
    "DesktopApplication",
    "DesktopLaunchError",
    "WindowGeometry",
    "build_parser",
    "default_webview_storage_path",
    "find_available_port",
    "main",
]
