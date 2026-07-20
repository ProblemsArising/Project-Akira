"""Authorized Discord direct-message handling for Project Akira.

Only one-to-one direct messages from users accepted by ``DiscordAccessPolicy``
reach the local conversation pipeline. Server messages, bots, blank messages,
and unauthorized users are ignored without a response. Authorized users are
routed to independent saved conversations through ``DiscordConversationSessions``.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Callable, Protocol

from app.discord_access import DiscordAccessPolicy, get_discord_access_policy
from app.discord_conversations import (
    DiscordConversationSessions,
    get_discord_conversation_sessions,
)


DISCORD_MESSAGE_LIMIT = 2_000
DEFAULT_FAILURE_REPLY = "Project Akira could not process that message."


class ConversationResultLike(Protocol):
    reply: str


class ConversationServiceLike(Protocol):
    def process_text(
        self,
        text: str,
        *,
        speak: bool = True,
        source: str = "text",
        audio_file: str | None = None,
    ) -> ConversationResultLike | None: ...


ConversationServiceFactory = Callable[[], ConversationServiceLike]


class DiscordDMOutcome(str, Enum):
    REPLIED = "replied"
    IGNORED_NON_DM = "ignored_non_dm"
    IGNORED_BOT = "ignored_bot"
    IGNORED_EMPTY = "ignored_empty"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DiscordDMResult:
    outcome: DiscordDMOutcome
    reply_parts: int = 0


def split_discord_message(
    text: str,
    *,
    limit: int = DISCORD_MESSAGE_LIMIT,
) -> tuple[str, ...]:
    """Split text into Discord-safe message parts without losing content."""

    if limit <= 0:
        raise ValueError("Discord message limit must be greater than zero")

    remaining = str(text).strip()
    if not remaining:
        return ()

    parts: list[str] = []
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        part = remaining[:split_at].rstrip()
        if not part:
            part = remaining[:limit]
            split_at = limit

        parts.append(part)
        remaining = remaining[split_at:].lstrip()

    if remaining:
        parts.append(remaining)

    return tuple(parts)


@asynccontextmanager
async def _typing_indicator(channel: Any) -> AsyncIterator[None]:
    typing = getattr(channel, "typing", None)
    if not callable(typing):
        yield
        return

    context = typing()
    async with context:
        yield


class DiscordDMHandler:
    """Authorize and process Discord DMs through ``ConversationService``."""

    def __init__(
        self,
        *,
        access_policy: DiscordAccessPolicy | None = None,
        service_factory: ConversationServiceFactory | None = None,
        conversation_sessions: DiscordConversationSessions | None = None,
        failure_reply: str = DEFAULT_FAILURE_REPLY,
    ) -> None:
        normalized_failure = str(failure_reply).strip()
        if not normalized_failure:
            raise ValueError("Discord failure reply cannot be blank")

        if service_factory is not None and conversation_sessions is not None:
            raise ValueError(
                "Pass either service_factory or conversation_sessions, not both"
            )

        self._access_policy = access_policy or get_discord_access_policy()
        if conversation_sessions is not None:
            self._conversation_sessions = conversation_sessions
        elif service_factory is not None:
            self._conversation_sessions = DiscordConversationSessions(
                service_factory=lambda user_id: service_factory(),
            )
        else:
            self._conversation_sessions = get_discord_conversation_sessions()
        self._failure_reply = normalized_failure

    async def handle_message(self, message: Any) -> DiscordDMResult:
        if getattr(message, "guild", None) is not None:
            return DiscordDMResult(DiscordDMOutcome.IGNORED_NON_DM)

        author = getattr(message, "author", None)
        if author is None or bool(getattr(author, "bot", False)):
            return DiscordDMResult(DiscordDMOutcome.IGNORED_BOT)

        if not self._access_policy.is_allowed(author):
            return DiscordDMResult(DiscordDMOutcome.DENIED)

        content = str(getattr(message, "content", "")).strip()
        if not content:
            return DiscordDMResult(DiscordDMOutcome.IGNORED_EMPTY)

        channel = getattr(message, "channel", None)
        send = getattr(channel, "send", None)
        if not callable(send):
            return DiscordDMResult(DiscordDMOutcome.FAILED)

        try:
            async with _typing_indicator(channel):
                result = await asyncio.to_thread(
                    self._conversation_sessions.process_text,
                    author,
                    content,
                    speak=False,
                    source="text",
                )

            reply = "" if result is None else str(result.reply).strip()
            parts = split_discord_message(reply or self._failure_reply)
            for part in parts:
                await send(part)

            return DiscordDMResult(
                DiscordDMOutcome.REPLIED,
                reply_parts=len(parts),
            )
        except Exception:
            await send(self._failure_reply)
            return DiscordDMResult(
                DiscordDMOutcome.FAILED,
                reply_parts=1,
            )

    async def __call__(self, message: Any) -> DiscordDMResult:
        return await self.handle_message(message)


_DEFAULT_DM_HANDLER: DiscordDMHandler | None = None
_DEFAULT_DM_HANDLER_LOCK = threading.Lock()


def get_discord_dm_handler() -> DiscordDMHandler:
    global _DEFAULT_DM_HANDLER
    with _DEFAULT_DM_HANDLER_LOCK:
        if _DEFAULT_DM_HANDLER is None:
            _DEFAULT_DM_HANDLER = DiscordDMHandler()
        return _DEFAULT_DM_HANDLER
