"""SQLite-backed chat history for Project Akira.

Long-term memory and chat history serve different purposes:

- ``ai/memory.py`` stores a compact set of facts and recent/relevant turns for
  prompting the LLM.
- This module stores complete user/Akira turns for the future WebUI history
  viewer, search, and conversation management.

Only Python's standard library is used so history is available in every build.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal, cast

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY_FILE = PROJECT_ROOT / "data" / "chat_history.db"
HISTORY_ENV_VAR = "AKIRA_HISTORY_FILE"
SCHEMA_VERSION = 1

ConversationSource = Literal["voice", "text"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _history_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()

    environment_path = os.getenv(HISTORY_ENV_VAR)
    if environment_path:
        return Path(environment_path).expanduser().resolve()

    return DEFAULT_HISTORY_FILE


def _title_from_text(text: str, max_length: int = 80) -> str:
    normalized = " ".join(str(text).split()).strip()
    if not normalized:
        return "New conversation"
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max(1, max_length - 1)].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """Small conversation record used by a future history sidebar."""

    id: int
    title: str
    created_at: str
    updated_at: str
    turn_count: int
    last_message: str


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """One persisted user-to-Akira turn."""

    id: int
    conversation_id: int
    user_text: str
    assistant_text: str
    source: ConversationSource
    audio_file: str | None
    spoken: bool
    created_at: str


class ChatHistoryStore:
    """Thread-safe SQLite access for Project Akira chat history.

    A fresh SQLite connection is used per operation. This avoids SQLite's
    default same-thread connection restriction when the future WebUI submits
    work from background threads.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = _history_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source IN ('voice', 'text')),
                    audio_file TEXT,
                    spoken INTEGER NOT NULL DEFAULT 0 CHECK (spoken IN (0, 1)),
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id)
                        REFERENCES conversations(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_turns_conversation_id
                    ON turns(conversation_id, id);

                CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
                    ON conversations(updated_at DESC, id DESC);
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create_conversation(self, title: str | None = None) -> int:
        """Create an empty conversation and return its database ID."""

        now = _utc_now()
        safe_title = _title_from_text(title or "New conversation")

        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conversations (title, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (safe_title, now, now),
            )
            return int(cursor.lastrowid)

    def record_turn(
        self,
        *,
        conversation_id: int | None,
        user_text: str,
        assistant_text: str,
        source: ConversationSource,
        audio_file: str | None = None,
        spoken: bool = False,
        title: str | None = None,
    ) -> tuple[int, int]:
        """Persist a completed turn atomically.

        If ``conversation_id`` is ``None`` a new conversation is created using
        the first user message as its title. Returns ``(conversation_id,
        turn_id)``.
        """

        normalized_user = str(user_text).strip()
        normalized_assistant = str(assistant_text).strip()
        if not normalized_user:
            raise ValueError("user_text cannot be blank")
        if not normalized_assistant:
            raise ValueError("assistant_text cannot be blank")
        if source not in ("voice", "text"):
            raise ValueError(f"Unsupported conversation source: {source!r}")

        now = _utc_now()

        with self._lock, self._connection() as connection:
            if conversation_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO conversations (title, created_at, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (_title_from_text(title or normalized_user), now, now),
                )
                conversation_id = int(cursor.lastrowid)
            else:
                exists = connection.execute(
                    "SELECT 1 FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                if exists is None:
                    raise KeyError(f"Conversation {conversation_id} does not exist")

            cursor = connection.execute(
                """
                INSERT INTO turns (
                    conversation_id,
                    user_text,
                    assistant_text,
                    source,
                    audio_file,
                    spoken,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    normalized_user,
                    normalized_assistant,
                    source,
                    audio_file,
                    int(bool(spoken)),
                    now,
                ),
            )
            turn_id = int(cursor.lastrowid)

            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

            return conversation_id, turn_id

    def list_conversations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        query: str | None = None,
    ) -> list[ConversationSummary]:
        """Return recent conversations newest-first.

        When ``query`` is provided, titles and complete user/Akira turn text are
        searched case-insensitively. Results stay grouped by conversation so the
        WebUI can show one concise history card per chat.
        """

        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        normalized_query = str(query or "").strip()

        where = ""
        parameters: list[object] = []
        if normalized_query:
            pattern = f"%{normalized_query}%"
            where = """
                WHERE c.title LIKE ? COLLATE NOCASE
                   OR EXISTS (
                       SELECT 1
                       FROM turns AS searched_turn
                       WHERE searched_turn.conversation_id = c.id
                         AND (
                             searched_turn.user_text LIKE ? COLLATE NOCASE
                             OR searched_turn.assistant_text LIKE ? COLLATE NOCASE
                         )
                   )
            """
            parameters.extend((pattern, pattern, pattern))

        parameters.extend((safe_limit, safe_offset))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    c.id,
                    c.title,
                    c.created_at,
                    c.updated_at,
                    COUNT(t.id) AS turn_count,
                    COALESCE((
                        SELECT t2.assistant_text
                        FROM turns AS t2
                        WHERE t2.conversation_id = c.id
                        ORDER BY t2.id DESC
                        LIMIT 1
                    ), '') AS last_message
                FROM conversations AS c
                LEFT JOIN turns AS t ON t.conversation_id = c.id
                {where}
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.id DESC
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()

        return [self._summary_from_row(row) for row in rows]

    def get_conversation(self, conversation_id: int) -> ConversationSummary | None:
        """Return one conversation summary, or ``None`` when it does not exist."""

        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    c.id,
                    c.title,
                    c.created_at,
                    c.updated_at,
                    COUNT(t.id) AS turn_count,
                    COALESCE((
                        SELECT t2.assistant_text
                        FROM turns AS t2
                        WHERE t2.conversation_id = c.id
                        ORDER BY t2.id DESC
                        LIMIT 1
                    ), '') AS last_message
                FROM conversations AS c
                LEFT JOIN turns AS t ON t.conversation_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (int(conversation_id),),
            ).fetchone()

        return None if row is None else self._summary_from_row(row)

    def get_turns(
        self,
        conversation_id: int,
        *,
        limit: int | None = None,
    ) -> list[HistoryTurn]:
        """Return a conversation's turns oldest-first."""

        query = """
            SELECT
                id,
                conversation_id,
                user_text,
                assistant_text,
                source,
                audio_file,
                spoken,
                created_at
            FROM turns
            WHERE conversation_id = ?
            ORDER BY id ASC
        """
        parameters: list[object] = [int(conversation_id)]

        if limit is not None:
            query += " LIMIT ?"
            parameters.append(max(1, min(int(limit), 5000)))

        with self._lock, self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return [self._turn_from_row(row) for row in rows]

    def search_turns(self, query: str, *, limit: int = 50) -> list[HistoryTurn]:
        """Search user and assistant text, newest matches first."""

        normalized = str(query).strip()
        if not normalized:
            return []

        pattern = f"%{normalized}%"
        safe_limit = max(1, min(int(limit), 500))

        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    conversation_id,
                    user_text,
                    assistant_text,
                    source,
                    audio_file,
                    spoken,
                    created_at
                FROM turns
                WHERE user_text LIKE ? COLLATE NOCASE
                   OR assistant_text LIKE ? COLLATE NOCASE
                ORDER BY id DESC
                LIMIT ?
                """,
                (pattern, pattern, safe_limit),
            ).fetchall()

        return [self._turn_from_row(row) for row in rows]

    def rename_conversation(self, conversation_id: int, title: str) -> None:
        """Rename a conversation for the future WebUI."""

        normalized = _title_from_text(title)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized, _utc_now(), int(conversation_id)),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Conversation {conversation_id} does not exist")

    def delete_conversation(self, conversation_id: int) -> bool:
        """Delete a conversation and all of its turns."""

        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE id = ?",
                (int(conversation_id),),
            )
            return cursor.rowcount > 0

    def clear_history(self) -> None:
        """Delete all stored conversations and turns."""

        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM conversations")

    def count_conversations(self) -> int:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM conversations"
            ).fetchone()
        return int(row["count"])

    @staticmethod
    def _summary_from_row(row: sqlite3.Row) -> ConversationSummary:
        return ConversationSummary(
            id=int(row["id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            turn_count=int(row["turn_count"]),
            last_message=str(row["last_message"]),
        )

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> HistoryTurn:
        return HistoryTurn(
            id=int(row["id"]),
            conversation_id=int(row["conversation_id"]),
            user_text=str(row["user_text"]),
            assistant_text=str(row["assistant_text"]),
            source=cast(ConversationSource, str(row["source"])),
            audio_file=(str(row["audio_file"]) if row["audio_file"] is not None else None),
            spoken=bool(row["spoken"]),
            created_at=str(row["created_at"]),
        )


def get_history_store(path: str | Path | None = None) -> ChatHistoryStore:
    """Create the default history store.

    The store itself is lightweight and opens connections only while an
    operation is running, so callers do not need to explicitly close it.
    """

    return ChatHistoryStore(path)
