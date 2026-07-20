"""Persistent per-user Discord conversation routing for Project Akira.

Each authorized Discord user receives a dedicated ``ConversationService`` and
an independent short-term LLM context. The current saved conversation ID is
stored in a small JSON mapping next to the chat-history database so the same
conversation can be restored after Project Akira restarts.

Issue #72 uses ``start_new_conversation`` for the Discord ``/new`` command.
"""

from __future__ import annotations

import json
import os
import threading

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Protocol

from app.discord_access import normalize_discord_user_id


DISCORD_CONVERSATION_MAP_ENV_VAR = "AKIRA_DISCORD_CONVERSATION_MAP_FILE"
DISCORD_CONVERSATION_MAP_FILENAME = "discord_conversations.json"
MAP_SCHEMA_VERSION = 1


class ConversationResultLike(Protocol):
    reply: str


class ConversationServiceLike(Protocol):
    @property
    def current_conversation_id(self) -> int | None: ...

    def activate_conversation(self, conversation_id: int) -> int: ...

    def start_new_conversation(self, title: str | None = None) -> int | None: ...

    def process_text(
        self,
        text: str,
        *,
        speak: bool = True,
        source: str = "text",
        audio_file: str | None = None,
    ) -> ConversationResultLike | None: ...


class HistoryStoreLike(Protocol):
    path: Path

    def get_conversation(self, conversation_id: int) -> object | None: ...


ConversationServiceFactory = Callable[[int], ConversationServiceLike]


class ConversationMapLike(Protocol):
    def conversation_for(self, user_or_id: object) -> int | None: ...

    def bind(self, user_or_id: object, conversation_id: int) -> None: ...

    def unbind(self, user_or_id: object) -> bool: ...

    def snapshot(self) -> "DiscordConversationMapSnapshot": ...


@dataclass(frozen=True, slots=True)
class DiscordConversationMapSnapshot:
    mapped_user_count: int
    persistent: bool


@dataclass(frozen=True, slots=True)
class DiscordConversationSessionSnapshot:
    active_session_count: int
    mapped_user_count: int


class InMemoryDiscordConversationMap:
    """Non-persistent mapping used by tests and injected integrations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mapping: dict[int, int] = {}

    def conversation_for(self, user_or_id: object) -> int | None:
        user_id = normalize_discord_user_id(user_or_id)
        with self._lock:
            return self._mapping.get(user_id)

    def bind(self, user_or_id: object, conversation_id: int) -> None:
        user_id = normalize_discord_user_id(user_or_id)
        normalized_conversation_id = int(conversation_id)
        if normalized_conversation_id <= 0:
            raise ValueError("Conversation ID must be a positive integer")
        with self._lock:
            self._mapping[user_id] = normalized_conversation_id

    def unbind(self, user_or_id: object) -> bool:
        user_id = normalize_discord_user_id(user_or_id)
        with self._lock:
            return self._mapping.pop(user_id, None) is not None

    def snapshot(self) -> DiscordConversationMapSnapshot:
        with self._lock:
            count = len(self._mapping)
        return DiscordConversationMapSnapshot(
            mapped_user_count=count,
            persistent=False,
        )


class DiscordConversationMap:
    """Atomic JSON mapping from Discord user IDs to saved conversations."""

    def __init__(
        self,
        history_store: HistoryStoreLike,
        path: str | Path | None = None,
    ) -> None:
        self._history_store = history_store
        self.path = self._resolve_path(history_store, path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _resolve_path(
        history_store: HistoryStoreLike,
        path: str | Path | None,
    ) -> Path:
        if path is not None:
            return Path(path).expanduser().resolve()

        environment_path = os.getenv(DISCORD_CONVERSATION_MAP_ENV_VAR)
        if environment_path:
            return Path(environment_path).expanduser().resolve()

        history_path = Path(history_store.path).expanduser().resolve()
        return history_path.with_name(DISCORD_CONVERSATION_MAP_FILENAME)

    @staticmethod
    def _empty_data() -> dict[str, object]:
        return {
            "schema_version": MAP_SCHEMA_VERSION,
            "users": {},
        }

    def _backup_invalid_file(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(
            f"{self.path.stem}.broken-{timestamp}{self.path.suffix}"
        )
        try:
            self.path.replace(backup)
        except OSError:
            pass

    def _load_unlocked(self) -> dict[str, int]:
        if not self.path.exists():
            return {}

        try:
            with self.path.open("r", encoding="utf-8") as file:
                raw = json.load(file)
        except json.JSONDecodeError:
            self._backup_invalid_file()
            return {}

        if not isinstance(raw, Mapping):
            self._backup_invalid_file()
            return {}

        users = raw.get("users", {})
        if not isinstance(users, Mapping):
            self._backup_invalid_file()
            return {}

        mapping: dict[str, int] = {}
        for raw_user_id, raw_conversation_id in users.items():
            try:
                user_id = normalize_discord_user_id(raw_user_id)
                conversation_id = int(raw_conversation_id)
            except (TypeError, ValueError):
                continue
            if conversation_id > 0:
                mapping[str(user_id)] = conversation_id
        return mapping

    def _save_unlocked(self, mapping: Mapping[str, int]) -> None:
        payload = {
            "schema_version": MAP_SCHEMA_VERSION,
            "users": dict(sorted(mapping.items(), key=lambda item: int(item[0]))),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")
        temporary.replace(self.path)

    def conversation_for(self, user_or_id: object) -> int | None:
        """Return a valid saved conversation ID for the Discord user."""

        user_id = normalize_discord_user_id(user_or_id)
        key = str(user_id)
        with self._lock:
            mapping = self._load_unlocked()
            conversation_id = mapping.get(key)
            if conversation_id is None:
                return None

            if self._history_store.get_conversation(conversation_id) is not None:
                return conversation_id

            # History deletion may invalidate a saved mapping. Clean it up
            # immediately instead of repeatedly trying to restore it.
            mapping.pop(key, None)
            self._save_unlocked(mapping)
            return None

    def bind(self, user_or_id: object, conversation_id: int) -> None:
        """Persist the user's current saved conversation."""

        user_id = normalize_discord_user_id(user_or_id)
        normalized_conversation_id = int(conversation_id)
        if normalized_conversation_id <= 0:
            raise ValueError("Conversation ID must be a positive integer")
        if self._history_store.get_conversation(normalized_conversation_id) is None:
            raise KeyError(
                f"Conversation {normalized_conversation_id} does not exist"
            )

        with self._lock:
            mapping = self._load_unlocked()
            mapping[str(user_id)] = normalized_conversation_id
            self._save_unlocked(mapping)

    def unbind(self, user_or_id: object) -> bool:
        user_id = normalize_discord_user_id(user_or_id)
        key = str(user_id)
        with self._lock:
            mapping = self._load_unlocked()
            if key not in mapping:
                return False
            mapping.pop(key)
            self._save_unlocked(mapping)
            return True

    def snapshot(self) -> DiscordConversationMapSnapshot:
        with self._lock:
            count = len(self._load_unlocked())
        return DiscordConversationMapSnapshot(
            mapped_user_count=count,
            persistent=True,
        )


class LazyDiscordConversationMap:
    """Create the default persistent mapping only when Discord is first used."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._delegate: DiscordConversationMap | None = None

    def _mapping(self) -> DiscordConversationMap:
        with self._lock:
            if self._delegate is None:
                from app.history import get_history_store

                self._delegate = DiscordConversationMap(get_history_store())
            return self._delegate

    def conversation_for(self, user_or_id: object) -> int | None:
        return self._mapping().conversation_for(user_or_id)

    def bind(self, user_or_id: object, conversation_id: int) -> None:
        self._mapping().bind(user_or_id, conversation_id)

    def unbind(self, user_or_id: object) -> bool:
        return self._mapping().unbind(user_or_id)

    def snapshot(self) -> DiscordConversationMapSnapshot:
        return self._mapping().snapshot()


class DiscordConversationSessions:
    """Create, restore, and track one conversation service per Discord user."""

    def __init__(
        self,
        *,
        service_factory: ConversationServiceFactory,
        conversation_map: ConversationMapLike | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._conversation_map = (
            conversation_map or InMemoryDiscordConversationMap()
        )
        self._lock = threading.RLock()
        self._services: dict[int, ConversationServiceLike] = {}

    def service_for(self, user_or_id: object) -> ConversationServiceLike:
        user_id = normalize_discord_user_id(user_or_id)
        with self._lock:
            existing = self._services.get(user_id)
            if existing is not None:
                return existing

            service = self._service_factory(user_id)
            saved_conversation_id = self._conversation_map.conversation_for(user_id)
            if saved_conversation_id is not None:
                try:
                    service.activate_conversation(saved_conversation_id)
                except KeyError:
                    self._conversation_map.unbind(user_id)

            self._services[user_id] = service
            return service

    def process_text(
        self,
        user_or_id: object,
        text: str,
        *,
        speak: bool = False,
        source: str = "text",
    ) -> ConversationResultLike | None:
        """Process a turn and persist the user's resulting conversation ID."""

        user_id = normalize_discord_user_id(user_or_id)
        service = self.service_for(user_id)
        result = service.process_text(
            text,
            speak=speak,
            source=source,
        )

        conversation_id = getattr(service, "current_conversation_id", None)
        if result is not None and conversation_id is not None:
            self._conversation_map.bind(user_id, int(conversation_id))
        return result

    def start_new_conversation(
        self,
        user_or_id: object,
        title: str | None = None,
    ) -> int | None:
        """Start and map a fresh saved conversation for one Discord user."""

        user_id = normalize_discord_user_id(user_or_id)
        service = self.service_for(user_id)
        conversation_id = service.start_new_conversation(title)
        if conversation_id is None:
            self._conversation_map.unbind(user_id)
        else:
            self._conversation_map.bind(user_id, conversation_id)
        return conversation_id

    def forget_runtime_session(self, user_or_id: object) -> bool:
        """Drop the in-memory service while preserving its saved mapping."""

        user_id = normalize_discord_user_id(user_or_id)
        with self._lock:
            return self._services.pop(user_id, None) is not None

    def clear_user(self, user_or_id: object) -> bool:
        """Drop both the in-memory service and its saved mapping."""

        removed_runtime = self.forget_runtime_session(user_or_id)
        removed_mapping = self._conversation_map.unbind(user_or_id)
        return removed_runtime or removed_mapping

    def snapshot(self) -> DiscordConversationSessionSnapshot:
        with self._lock:
            active_count = len(self._services)
        return DiscordConversationSessionSnapshot(
            active_session_count=active_count,
            mapped_user_count=self._conversation_map.snapshot().mapped_user_count,
        )


def _create_default_sessions() -> DiscordConversationSessions:
    def create_service(user_id: int) -> ConversationServiceLike:
        del user_id
        from ai.llm import LocalLLM
        from app.conversation import ConversationService
        from app.history import get_history_store

        llm = LocalLLM()
        return ConversationService(
            recorder=lambda: None,
            transcriber=lambda audio_file: "",
            responder=llm.ask,
            speaker=lambda reply: None,
            on_reply=None,
            history_store=get_history_store(),
            load_conversation_context=llm.load_short_term_history,
            reset_conversation_context=llm.reset_short_term_memory,
        )

    return DiscordConversationSessions(
        service_factory=create_service,
        conversation_map=LazyDiscordConversationMap(),
    )


_DEFAULT_SESSIONS: DiscordConversationSessions | None = None
_DEFAULT_SESSIONS_LOCK = threading.Lock()


def get_discord_conversation_sessions() -> DiscordConversationSessions:
    """Return the process-wide Discord conversation session manager."""

    global _DEFAULT_SESSIONS
    with _DEFAULT_SESSIONS_LOCK:
        if _DEFAULT_SESSIONS is None:
            _DEFAULT_SESSIONS = _create_default_sessions()
        return _DEFAULT_SESSIONS
