"""Discord client lifecycle service for Project Akira.

This module intentionally owns only the Discord connection lifecycle. Message
authorization, DM handling, conversation mapping, commands, health reporting,
and settings integration are implemented by later Discord milestone issues.

``discord.py`` is imported lazily by the default client factory so importing
Project Akira's core modules and running unit tests does not require Discord
support to initialize.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


class DiscordClient(Protocol):
    """Minimum async client API required by ``DiscordAdapterService``."""

    async def start(self, token: str, *, reconnect: bool = False) -> None: ...

    async def close(self) -> None: ...

    def is_closed(self) -> bool: ...


DiscordClientFactory = Callable[[], DiscordClient]
DiscordErrorCallback = Callable[[Exception], None]


class DiscordAdapterState(str, Enum):
    """Lifecycle state exposed by the Discord adapter."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DiscordAdapterSnapshot:
    """Thread-safe public view of the current adapter state."""

    state: DiscordAdapterState
    running: bool
    last_error: str | None = None


def create_default_discord_client() -> DiscordClient:
    """Create the production ``discord.py`` client without importing it early."""

    try:
        import discord
    except ImportError as error:
        raise RuntimeError(
            "Discord support requires discord.py. Install Project Akira's "
            "requirements before starting the Discord adapter."
        ) from error

    # Issue #70 will enable and consume the message-related intents when DM
    # handling is added. No privileged intents are needed for lifecycle setup.
    intents = discord.Intents.none()
    return discord.Client(intents=intents)


class DiscordAdapterService:
    """Run a Discord client on an isolated asyncio loop and worker thread.

    ``start`` accepts a token directly but does not persist it. Issue #68 adds
    secure token storage and will supply the retrieved token to this service.
    The service is deliberately independent from FastAPI and the desktop UI so
    both can control the same lifecycle API later.
    """

    def __init__(
        self,
        *,
        client_factory: DiscordClientFactory | None = None,
        on_error: DiscordErrorCallback | None = None,
    ) -> None:
        self._client_factory = client_factory or create_default_discord_client
        self._on_error = on_error

        self._lock = threading.RLock()
        self._state = DiscordAdapterState.STOPPED
        self._last_error: Exception | None = None

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: DiscordClient | None = None

        self._started_event = threading.Event()
        self._stopped_event = threading.Event()
        self._stopped_event.set()
        self._stop_requested = threading.Event()

    @property
    def state(self) -> DiscordAdapterState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state in {
                DiscordAdapterState.STARTING,
                DiscordAdapterState.RUNNING,
                DiscordAdapterState.STOPPING,
            }

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error

    def snapshot(self) -> DiscordAdapterSnapshot:
        """Return a stable status object suitable for APIs and future UI code."""

        with self._lock:
            error = None if self._last_error is None else str(self._last_error)
            return DiscordAdapterSnapshot(
                state=self._state,
                running=self._state
                in {
                    DiscordAdapterState.STARTING,
                    DiscordAdapterState.RUNNING,
                    DiscordAdapterState.STOPPING,
                },
                last_error=error,
            )

    def start(self, token: str, *, timeout: float = 5.0) -> bool:
        """Start the Discord client in the background.

        Returns ``False`` when this service is already active. A blank token is
        rejected before a worker is created. The token is passed only as the
        worker argument and is never saved on the service instance.
        """

        normalized_token = str(token).strip()
        if not normalized_token:
            raise ValueError("Discord bot token cannot be blank")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False

            self._state = DiscordAdapterState.STARTING
            self._last_error = None
            self._loop = None
            self._client = None
            self._started_event.clear()
            self._stopped_event.clear()
            self._stop_requested.clear()

            thread = threading.Thread(
                target=self._thread_main,
                args=(normalized_token,),
                name="ProjectAkiraDiscord",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        if not self._started_event.wait(timeout=timeout):
            self._stop_requested.set()
            thread.join(timeout=timeout)
            raise TimeoutError(
                "Discord adapter worker did not initialize within "
                f"{timeout:.1f} seconds"
            )

        with self._lock:
            if self._state is DiscordAdapterState.FAILED:
                error = self._last_error
                raise RuntimeError("Discord adapter failed to start") from error

        return True

    def stop(self, *, timeout: float = 5.0) -> bool:
        """Close the active Discord client and wait for its thread to exit."""

        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        deadline = time.monotonic() + timeout
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return False

            self._stop_requested.set()
            if self._state is not DiscordAdapterState.FAILED:
                self._state = DiscordAdapterState.STOPPING
            loop = self._loop
            client = self._client

        if loop is not None and client is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._close_client(client),
                loop,
            )
            remaining = max(0.0, deadline - time.monotonic())
            try:
                future.result(timeout=remaining)
            except concurrent.futures.CancelledError:
                # The worker may finish and close its loop at the same moment.
                pass
            except concurrent.futures.TimeoutError:
                pass
            except Exception as error:
                self._record_failure(error)

        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)
        if thread.is_alive():
            raise TimeoutError(
                "Discord adapter did not stop within "
                f"{timeout:.1f} seconds"
            )

        return True

    def wait_until_stopped(self, timeout: float | None = None) -> bool:
        """Wait for the worker to terminate without changing its state."""

        return self._stopped_event.wait(timeout=timeout)

    def _thread_main(self, token: str) -> None:
        try:
            asyncio.run(self._run_client(token))
        except Exception as error:
            self._record_failure(error)
            self._started_event.set()
        finally:
            with self._lock:
                self._loop = None
                self._client = None
                if self._state is not DiscordAdapterState.FAILED:
                    self._state = DiscordAdapterState.STOPPED
            self._stopped_event.set()

    async def _run_client(self, token: str) -> None:
        loop = asyncio.get_running_loop()
        client = self._client_factory()

        with self._lock:
            self._loop = loop
            self._client = client
            if self._stop_requested.is_set():
                self._state = DiscordAdapterState.STOPPING
            else:
                self._state = DiscordAdapterState.RUNNING
            self._started_event.set()

        if self._stop_requested.is_set():
            await self._close_client(client)
            return

        try:
            # Reconnect policy and connection health are added by issue #73.
            await client.start(token, reconnect=False)
        finally:
            await self._close_client(client)

    @staticmethod
    async def _close_client(client: DiscordClient) -> None:
        if not client.is_closed():
            await client.close()

    def _record_failure(self, error: Exception) -> None:
        callback = None
        with self._lock:
            self._last_error = error
            self._state = DiscordAdapterState.FAILED
            callback = self._on_error

        if callback is not None:
            try:
                callback(error)
            except Exception:
                # Diagnostic callbacks must never kill or mask the worker.
                pass


_DEFAULT_SERVICE: DiscordAdapterService | None = None
_DEFAULT_SERVICE_LOCK = threading.Lock()


def get_discord_adapter_service() -> DiscordAdapterService:
    """Return the process-wide Discord adapter service."""

    global _DEFAULT_SERVICE
    with _DEFAULT_SERVICE_LOCK:
        if _DEFAULT_SERVICE is None:
            _DEFAULT_SERVICE = DiscordAdapterService()
        return _DEFAULT_SERVICE
