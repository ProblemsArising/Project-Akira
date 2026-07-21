"""Text commands for authorized Project Akira Discord DMs."""

from __future__ import annotations

import threading

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from app.discord_access import DiscordAccessPolicy, get_discord_access_policy
from app.discord_conversations import DiscordConversationSessions


DEFAULT_NEW_TITLE = "Discord conversation"
AVAILABLE_COMMANDS = "/new, /status, /stop"


class DiscordCommandName(str, Enum):
    NEW = "new"
    STATUS = "status"
    STOP = "stop"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DiscordRemoteStatus:
    """Non-secret status data displayed by the ``/status`` command."""

    adapter_state: str
    token_configured: bool | None
    allowed_user_count: int
    active_session_count: int
    mapped_user_count: int
    adapter_health: str = "unavailable"
    gateway_connected: bool | None = None
    gateway_ready: bool | None = None
    reconnect_enabled: bool | None = None
    reconnect_count: int = 0
    disconnect_count: int = 0
    latency_ms: float | None = None
    uptime_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class DiscordCommandResult:
    """Result returned to the DM handler for one command."""

    command: DiscordCommandName
    reply: str
    stop_after_reply: bool = False


StatusProvider = Callable[[], DiscordRemoteStatus]
StopCallback = Callable[[], object]


def parse_discord_command(content: str) -> tuple[str, str] | None:
    """Parse a slash-prefixed DM command and its optional argument text."""

    normalized = str(content).strip()
    if not normalized.startswith("/"):
        return None

    command, separator, arguments = normalized.partition(" ")
    return command.casefold(), arguments.strip() if separator else ""


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unavailable"

    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_discord_status(status: DiscordRemoteStatus) -> str:
    """Render connection health without credentials, IDs, or error details."""

    if status.token_configured is True:
        token_status = "configured"
    elif status.token_configured is False:
        token_status = "not configured"
    else:
        token_status = "unavailable"

    if status.gateway_ready is True:
        gateway_status = "ready"
    elif status.gateway_connected is True:
        gateway_status = "connected"
    elif status.gateway_connected is False:
        gateway_status = "disconnected"
    else:
        gateway_status = "unavailable"

    if status.reconnect_enabled is True:
        reconnect_status = "enabled"
    elif status.reconnect_enabled is False:
        reconnect_status = "disabled"
    else:
        reconnect_status = "unavailable"

    recovery_label = "recovery" if status.reconnect_count == 1 else "recoveries"
    disconnect_label = (
        "disconnect" if status.disconnect_count == 1 else "disconnects"
    )

    allowed_label = "user" if status.allowed_user_count == 1 else "users"
    active_label = "session" if status.active_session_count == 1 else "sessions"
    saved_label = (
        "conversation" if status.mapped_user_count == 1 else "conversations"
    )

    lines = [
        "Project Akira Discord status",
        f"- Connection: {status.adapter_state}",
        f"- Health: {status.adapter_health}",
        f"- Gateway: {gateway_status}",
        (
            f"- Reconnect: {reconnect_status} "
            f"({status.reconnect_count} {recovery_label}, "
            f"{status.disconnect_count} {disconnect_label})"
        ),
    ]

    if status.latency_ms is not None:
        lines.append(f"- Latency: {status.latency_ms:.0f} ms")
    if status.uptime_seconds is not None:
        lines.append(f"- Uptime: {_format_duration(status.uptime_seconds)}")

    lines.extend(
        [
            f"- Bot token: {token_status}",
            f"- Access: {status.allowed_user_count} allowed {allowed_label}",
            f"- Runtime: {status.active_session_count} active {active_label}",
            f"- Saved: {status.mapped_user_count} mapped {saved_label}",
        ]
    )
    return "\n".join(lines)


class DiscordCommandRouter:
    """Execute authorized text commands without sending them to the LLM."""

    def __init__(
        self,
        *,
        conversation_sessions: DiscordConversationSessions,
        access_policy: DiscordAccessPolicy | None = None,
        status_provider: StatusProvider | None = None,
        stop_callback: StopCallback | None = None,
    ) -> None:
        self._conversation_sessions = conversation_sessions
        self._access_policy = access_policy or get_discord_access_policy()
        self._status_provider = status_provider or self._default_status
        self._stop_callback = stop_callback or self._default_stop

    def handle(
        self,
        user_or_id: object,
        content: str,
    ) -> DiscordCommandResult | None:
        """Handle a command or return ``None`` for a normal chat message."""

        parsed = parse_discord_command(content)
        if parsed is None:
            return None

        command, arguments = parsed
        if command == "/new":
            title = arguments or DEFAULT_NEW_TITLE
            self._conversation_sessions.start_new_conversation(user_or_id, title)
            return DiscordCommandResult(
                command=DiscordCommandName.NEW,
                reply="Started a new Discord conversation.",
            )

        if command == "/status":
            if arguments:
                return DiscordCommandResult(
                    command=DiscordCommandName.STATUS,
                    reply="Usage: /status",
                )
            return DiscordCommandResult(
                command=DiscordCommandName.STATUS,
                reply=format_discord_status(self._status_provider()),
            )

        if command == "/stop":
            if arguments:
                return DiscordCommandResult(
                    command=DiscordCommandName.STOP,
                    reply="Usage: /stop",
                )
            return DiscordCommandResult(
                command=DiscordCommandName.STOP,
                reply="Stopping Discord remote messaging.",
                stop_after_reply=True,
            )

        return DiscordCommandResult(
            command=DiscordCommandName.UNKNOWN,
            reply=f"Unknown command. Available commands: {AVAILABLE_COMMANDS}.",
        )

    def schedule_stop(self) -> threading.Thread:
        """Stop the adapter outside its own Discord event-loop thread."""

        thread = threading.Thread(
            target=self._run_stop_callback,
            name="ProjectAkiraDiscordStop",
            daemon=True,
        )
        thread.start()
        return thread

    def _run_stop_callback(self) -> None:
        try:
            self._stop_callback()
        except Exception:
            pass

    def _default_status(self) -> DiscordRemoteStatus:
        adapter_state = "unavailable"
        adapter_health = "unavailable"
        gateway_connected: bool | None = None
        gateway_ready: bool | None = None
        reconnect_enabled: bool | None = None
        reconnect_count = 0
        disconnect_count = 0
        latency_ms: float | None = None
        uptime_seconds: float | None = None
        token_configured: bool | None = None

        try:
            from app.discord_adapter import get_discord_adapter_service

            adapter = get_discord_adapter_service().snapshot()
            adapter_state = adapter.state.value
            adapter_health = adapter.health.value
            gateway_connected = adapter.connected
            gateway_ready = adapter.ready
            reconnect_enabled = adapter.reconnect_enabled
            reconnect_count = adapter.reconnect_count
            disconnect_count = adapter.disconnect_count
            latency_ms = adapter.latency_ms
            uptime_seconds = adapter.uptime_seconds
        except Exception:
            pass

        try:
            from app.discord_token_store import get_discord_token_store

            token_configured = get_discord_token_store().status().configured
        except Exception:
            pass

        access = self._access_policy.snapshot()
        sessions = self._conversation_sessions.snapshot()
        return DiscordRemoteStatus(
            adapter_state=adapter_state,
            token_configured=token_configured,
            allowed_user_count=access.allowed_user_count,
            active_session_count=sessions.active_session_count,
            mapped_user_count=sessions.mapped_user_count,
            adapter_health=adapter_health,
            gateway_connected=gateway_connected,
            gateway_ready=gateway_ready,
            reconnect_enabled=reconnect_enabled,
            reconnect_count=reconnect_count,
            disconnect_count=disconnect_count,
            latency_ms=latency_ms,
            uptime_seconds=uptime_seconds,
        )

    @staticmethod
    def _default_stop() -> None:
        from app.discord_adapter import get_discord_adapter_service

        get_discord_adapter_service().stop(timeout=5.0)
