"""Discord client lifecycle, reconnect, and health service.

``discord.py`` is imported lazily by the default client factory so importing
Project Akira's core modules and running unit tests does not initialize Discord.

The client uses discord.py's built-in reconnect loop. Gateway events update a
thread-safe health snapshot separately from the worker-thread lifecycle.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import threading
import time

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol

from app.discord_errors import (
    DiscordUnavailableError,
    classify_discord_delivery_error,
)


class DiscordClient(Protocol):
    """Minimum async client API required by ``DiscordAdapterService``."""

    async def start(self, token: str, *, reconnect: bool = True) -> None: ...

    async def close(self) -> None: ...

    def is_closed(self) -> bool: ...


DiscordClientFactory = Callable[[], DiscordClient]
DiscordErrorCallback = Callable[[Exception], None]
DiscordMessageHandler = Callable[[Any], Awaitable[Any]]


class DiscordAdapterState(str, Enum):
    """Worker lifecycle state exposed by the Discord adapter."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class DiscordAdapterHealth(str, Enum):
    """Gateway health derived from lifecycle and connection events."""

    STOPPED = "stopped"
    CONNECTING = "connecting"
    HEALTHY = "healthy"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    FAILED = "failed"


class DiscordGatewayEvent(str, Enum):
    """Connection events emitted by the Discord client."""

    CONNECTED = "connected"
    READY = "ready"
    RESUMED = "resumed"
    DISCONNECTED = "disconnected"


DiscordGatewayEventHandler = Callable[[DiscordGatewayEvent], None]


@dataclass(frozen=True, slots=True)
class DiscordAdapterSnapshot:
    """Thread-safe, non-secret adapter and gateway health."""

    state: DiscordAdapterState
    running: bool
    last_error: str | None = None
    health: DiscordAdapterHealth = DiscordAdapterHealth.STOPPED
    connected: bool = False
    ready: bool = False
    reconnect_enabled: bool = True
    reconnect_count: int = 0
    disconnect_count: int = 0
    latency_ms: float | None = None
    uptime_seconds: float | None = None


def create_default_discord_client(
    message_handler: DiscordMessageHandler | None = None,
    gateway_event_handler: DiscordGatewayEventHandler | None = None,
) -> DiscordClient:
    """Create the production client and attach DM and health handlers."""

    try:
        import discord
    except ImportError as error:
        raise RuntimeError(
            "Discord support requires discord.py. Install Project Akira's "
            "requirements before starting the Discord adapter."
        ) from error

    intents = discord.Intents.none()
    intents.dm_messages = True
    client = discord.Client(intents=intents)

    def emit_gateway_event(event: DiscordGatewayEvent) -> None:
        if gateway_event_handler is None:
            return
        try:
            gateway_event_handler(event)
        except Exception:
            # Health reporting must never interrupt Discord's gateway events.
            pass

    if message_handler is not None:

        @client.event
        async def on_message(message: Any) -> None:
            await message_handler(message)

    if gateway_event_handler is not None:

        @client.event
        async def on_connect() -> None:
            emit_gateway_event(DiscordGatewayEvent.CONNECTED)

        @client.event
        async def on_ready() -> None:
            emit_gateway_event(DiscordGatewayEvent.READY)

        @client.event
        async def on_resumed() -> None:
            emit_gateway_event(DiscordGatewayEvent.RESUMED)

        @client.event
        async def on_disconnect() -> None:
            emit_gateway_event(DiscordGatewayEvent.DISCONNECTED)

    return client


class DiscordAdapterService:
    """Run a reconnecting Discord client on an isolated asyncio thread."""

    def __init__(
        self,
        *,
        client_factory: DiscordClientFactory | None = None,
        message_handler: DiscordMessageHandler | None = None,
        on_error: DiscordErrorCallback | None = None,
    ) -> None:
        if client_factory is None:
            self._client_factory = lambda: create_default_discord_client(
                message_handler,
                self.record_gateway_event,
            )
        else:
            self._client_factory = client_factory
        self._on_error = on_error

        self._lock = threading.RLock()
        self._state = DiscordAdapterState.STOPPED
        self._last_error: Exception | None = None

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: DiscordClient | None = None

        self._connected = False
        self._ready = False
        self._awaiting_reconnect = False
        self._reconnect_count = 0
        self._disconnect_count = 0
        self._started_monotonic: float | None = None

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
        """Return the current lifecycle and gateway health."""

        with self._lock:
            state = self._state
            running = state in {
                DiscordAdapterState.STARTING,
                DiscordAdapterState.RUNNING,
                DiscordAdapterState.STOPPING,
            }
            error = None if self._last_error is None else str(self._last_error)
            connected = self._connected
            ready = self._ready
            reconnect_count = self._reconnect_count
            disconnect_count = self._disconnect_count
            health = self._health_locked()
            started = self._started_monotonic
            client = self._client

        latency_ms = self._read_latency_ms(client) if ready else None
        uptime_seconds = None
        if running and started is not None:
            uptime_seconds = round(max(0.0, time.monotonic() - started), 1)

        return DiscordAdapterSnapshot(
            state=state,
            running=running,
            last_error=error,
            health=health,
            connected=connected,
            ready=ready,
            reconnect_enabled=True,
            reconnect_count=reconnect_count,
            disconnect_count=disconnect_count,
            latency_ms=latency_ms,
            uptime_seconds=uptime_seconds,
        )

    def start(self, token: str, *, timeout: float = 5.0) -> bool:
        """Start the Discord client in the background.

        The token is passed only to the worker and is not retained by the
        service. discord.py handles transient reconnects after startup.
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
            self._connected = False
            self._ready = False
            self._awaiting_reconnect = False
            self._reconnect_count = 0
            self._disconnect_count = 0
            self._started_monotonic = time.monotonic()
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
            self._awaiting_reconnect = False
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

    def send_direct_message(
        self,
        user_id: int,
        content: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        """Send one Discord-safe message from a non-async caller.

        The coroutine is scheduled on the adapter's existing gateway event
        loop. The adapter must be running and the Discord gateway must be
        ready before outbound delivery is accepted.
        """

        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("Discord user ID must be a positive integer")

        normalized_content = str(content).strip()
        if not normalized_content:
            raise ValueError("Discord direct message cannot be blank")
        if len(normalized_content) > 2_000:
            raise ValueError("Discord direct message cannot exceed 2000 characters")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        with self._lock:
            loop = self._loop
            client = self._client
            state = self._state
            ready = self._ready

        if (
            state is not DiscordAdapterState.RUNNING
            or not ready
            or loop is None
            or client is None
            or not loop.is_running()
        ):
            raise RuntimeError("Discord remote messaging is not ready")

        future = asyncio.run_coroutine_threadsafe(
            self._send_direct_message(client, user_id, normalized_content),
            loop,
        )
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as error:
            future.cancel()
            raise DiscordUnavailableError() from error
        except Exception as error:
            raise classify_discord_delivery_error(error) from error

    def record_gateway_event(
        self,
        event: DiscordGatewayEvent | str,
    ) -> None:
        """Record one Discord gateway event for health reporting."""

        normalized_event = DiscordGatewayEvent(event)
        with self._lock:
            active = self._state in {
                DiscordAdapterState.STARTING,
                DiscordAdapterState.RUNNING,
            }

            if normalized_event is DiscordGatewayEvent.CONNECTED:
                if active:
                    self._connected = True
                    self._ready = False
                return

            if normalized_event in {
                DiscordGatewayEvent.READY,
                DiscordGatewayEvent.RESUMED,
            }:
                if active:
                    if self._awaiting_reconnect:
                        self._reconnect_count += 1
                    self._connected = True
                    self._ready = True
                    self._awaiting_reconnect = False
                return

            self._connected = False
            self._ready = False
            if active and not self._stop_requested.is_set():
                self._disconnect_count += 1
                self._awaiting_reconnect = True

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
                self._connected = False
                self._ready = False
                self._awaiting_reconnect = False
                self._started_monotonic = None
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
            await client.start(token, reconnect=True)
        finally:
            await self._close_client(client)

    @staticmethod
    async def _send_direct_message(
        client: DiscordClient,
        user_id: int,
        content: str,
    ) -> None:
        get_user = getattr(client, "get_user", None)
        user = get_user(user_id) if callable(get_user) else None

        if user is None:
            fetch_user = getattr(client, "fetch_user", None)
            if not callable(fetch_user):
                raise RuntimeError("Discord client cannot resolve users")
            user = await fetch_user(user_id)

        send = getattr(user, "send", None)
        if not callable(send):
            raise RuntimeError("Discord user cannot receive direct messages")
        await send(content)

    @staticmethod
    async def _close_client(client: DiscordClient) -> None:
        if not client.is_closed():
            await client.close()

    @staticmethod
    def _read_latency_ms(client: DiscordClient | None) -> float | None:
        if client is None:
            return None

        try:
            latency_seconds = float(getattr(client, "latency"))
        except (AttributeError, TypeError, ValueError):
            return None

        if not math.isfinite(latency_seconds) or latency_seconds < 0:
            return None
        return round(latency_seconds * 1_000, 1)

    def _health_locked(self) -> DiscordAdapterHealth:
        if self._state is DiscordAdapterState.FAILED:
            return DiscordAdapterHealth.FAILED
        if self._state is DiscordAdapterState.STOPPING:
            return DiscordAdapterHealth.STOPPING
        if self._state is DiscordAdapterState.STOPPED:
            return DiscordAdapterHealth.STOPPED
        if self._connected and self._ready:
            return DiscordAdapterHealth.HEALTHY
        if self._awaiting_reconnect:
            return DiscordAdapterHealth.RECONNECTING
        return DiscordAdapterHealth.CONNECTING

    def _record_failure(self, error: Exception) -> None:
        callback = None
        with self._lock:
            self._last_error = error
            self._state = DiscordAdapterState.FAILED
            self._connected = False
            self._ready = False
            self._awaiting_reconnect = False
            callback = self._on_error

        if callback is not None:
            try:
                callback(error)
            except Exception:
                pass


_DEFAULT_SERVICE: DiscordAdapterService | None = None
_DEFAULT_SERVICE_LOCK = threading.Lock()


def get_discord_adapter_service() -> DiscordAdapterService:
    """Return the process-wide Discord adapter service."""

    global _DEFAULT_SERVICE
    with _DEFAULT_SERVICE_LOCK:
        if _DEFAULT_SERVICE is None:
            from app.discord_dm import get_discord_dm_handler

            _DEFAULT_SERVICE = DiscordAdapterService(
                message_handler=get_discord_dm_handler(),
            )
        return _DEFAULT_SERVICE
