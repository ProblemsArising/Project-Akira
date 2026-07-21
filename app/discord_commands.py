"""Text commands for authorized Project Akira Discord DMs.

These commands are intentionally handled before normal conversation messages:

``/new [title]``
    Start a fresh saved conversation for the requesting Discord user.

``/status``
    Show non-secret Discord configuration and session state.

``/stop``
    Confirm the request and then stop Discord remote messaging on a separate
    thread after the confirmation has been sent.

Issue #73 expands the basic status response with reconnect and health details.
"""

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


def format_discord_status(status: DiscordRemoteStatus) -> str:
    """Render status without including credentials or user IDs."""

    if status.token_configured is True:
        token_status = "configured"
    elif status.token_configured is False:
        token_status = "not configured"
    else:
        token_status = "unavailable"

    allowed_label = "user" if status.allowed_user_count == 1 else "users"
    active_label = "session" if status.active_session_count == 1 else "sessions"
    saved_label = (
        "conversation" if status.mapped_user_count == 1 else "conversations"
    )

    return (
        "Project Akira Discord status\n"
        f"- Connection: {status.adapter_state}\n"
        f"- Bot token: {token_status}\n"
        f"- Access: {status.allowed_user_count} allowed {allowed_label}\n"
        f"- Runtime: {status.active_session_count} active {active_label}\n"
        f"- Saved: {status.mapped_user_count} mapped {saved_label}"
    )


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
            # Issue #73 adds command-visible health/error reporting. A stop
            # request must not raise back into Discord's gateway event loop.
            pass

    def _default_status(self) -> DiscordRemoteStatus:
        adapter_state = "unavailable"
        token_configured: bool | None = None

        try:
            from app.discord_adapter import get_discord_adapter_service

            adapter_state = get_discord_adapter_service().snapshot().state.value
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
        )

    @staticmethod
    def _default_stop() -> None:
        from app.discord_adapter import get_discord_adapter_service

        get_discord_adapter_service().stop(timeout=5.0)
