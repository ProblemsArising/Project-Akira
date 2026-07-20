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
from collections.abc import Callable, Sequence
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
DEFAULT_STARTUP_TIMEOUT = 20.0


class DesktopLaunchError(RuntimeError):
    """Raised when the local backend or native window cannot be started."""


class ServerLike(Protocol):
    should_exit: bool
    started: bool

    def run(self) -> None: ...


ServerFactory = Callable[[Any], ServerLike]
ReadinessProbe = Callable[[str, float], bool]


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
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        maximized: bool = False,
        debug: bool = False,
        storage_path: Path | None = None,
        webview_module: Any | None = None,
    ) -> None:
        self.backend = backend or BackendServer()
        self.title = str(title)
        self.width = max(DEFAULT_MIN_SIZE[0], int(width))
        self.height = max(DEFAULT_MIN_SIZE[1], int(height))
        self.maximized = bool(maximized)
        self.debug = bool(debug)
        self.storage_path = storage_path or default_webview_storage_path()
        self._webview_module = webview_module

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

            webview.create_window(
                self.title,
                url,
                width=self.width,
                height=self.height,
                min_size=DEFAULT_MIN_SIZE,
                resizable=True,
                maximized=self.maximized,
                background_color="#0d0b16",
                text_select=True,
                zoomable=True,
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
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument(
        "--maximized",
        action="store_true",
        help="Open the desktop window maximized.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable pywebview debug mode and developer tools.",
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
        maximized=arguments.maximized,
        debug=arguments.debug,
    )

    try:
        return application.run()
    except DesktopLaunchError as error:
        print(f"Project Akira could not start: {error}")
        return 1


__all__ = [
    "BackendServer",
    "DesktopApplication",
    "DesktopLaunchError",
    "build_parser",
    "default_webview_storage_path",
    "find_available_port",
    "main",
]
