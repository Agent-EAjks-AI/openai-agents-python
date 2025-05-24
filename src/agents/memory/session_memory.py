from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..items import TResponseInputItem


@runtime_checkable
class SessionMemory(Protocol):
    """Protocol for session memory implementations.

    Session memory stores conversation history across agent runs, allowing
    agents to maintain context without requiring explicit manual memory management.
    """

    async def get_messages(self, session_id: str) -> list[TResponseInputItem]:
        """Retrieve the conversation history for a given session.

        Args:
            session_id: Unique identifier for the conversation session

        Returns:
            List of input items representing the conversation history
        """
        ...

    async def add_messages(
        self, session_id: str, messages: list[TResponseInputItem]
    ) -> None:
        """Add new messages to the conversation history.

        Args:
            session_id: Unique identifier for the conversation session
            messages: List of input items to add to the history
        """
        ...

    async def pop_message(self, session_id: str) -> TResponseInputItem | None:
        """Remove and return the most recent message from the session.

        Args:
            session_id: Unique identifier for the conversation session

        Returns:
            The most recent message if it exists, None if the session is empty
        """
        ...

    async def clear_session(self, session_id: str) -> None:
        """Clear all messages for a given session.

        Args:
            session_id: Unique identifier for the conversation session
        """
        ...


class SQLiteSessionMemory(SessionMemory):
    """SQLite-based implementation of session memory.

    This implementation stores conversation history in a SQLite database.
    By default, uses an in-memory database that is lost when the process ends.
    For persistent storage, provide a file path.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        sessions_table: str = "agent_sessions",
        messages_table: str = "agent_messages",
    ):
        """Initialize the SQLite session memory.

        Args:
            db_path: Path to the SQLite database file. Defaults to ':memory:' (in-memory database)
            sessions_table: Name of the table to store session metadata. Defaults to 'agent_sessions'
            messages_table: Name of the table to store message data. Defaults to 'agent_messages'
        """
        self.db_path = db_path
        self.sessions_table = sessions_table
        self.messages_table = messages_table
        self._local = threading.local()
        self._lock = threading.Lock()

        # For in-memory databases, we need a shared connection to avoid thread isolation
        # For file databases, we use thread-local connections for better concurrency
        self._is_memory_db = str(db_path) == ":memory:"
        if self._is_memory_db:
            self._shared_connection = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._shared_connection.execute("PRAGMA journal_mode=WAL")
            self._init_db_for_connection(self._shared_connection)

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        if self._is_memory_db:
            # Use shared connection for in-memory database to avoid thread isolation
            return self._shared_connection
        else:
            # Use thread-local connections for file databases
            if not hasattr(self._local, "connection"):
                self._local.connection = sqlite3.connect(
                    str(self.db_path),
                    check_same_thread=False,
                )
                self._local.connection.execute("PRAGMA journal_mode=WAL")
                # Initialize the database schema for this connection
                self._init_db_for_connection(self._local.connection)
            return self._local.connection

    def _init_db_for_connection(self, conn: sqlite3.Connection) -> None:
        """Initialize the database schema for a specific connection."""
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.sessions_table} (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.messages_table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES {self.sessions_table} (session_id) ON DELETE CASCADE
            )
        """
        )

        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self.messages_table}_session_id 
            ON {self.messages_table} (session_id, created_at)
        """
        )

        conn.commit()

    async def get_messages(self, session_id: str) -> list[TResponseInputItem]:
        """Retrieve the conversation history for a given session.

        Args:
            session_id: Unique identifier for the conversation session

        Returns:
            List of input items representing the conversation history
        """

        def _get_messages_sync():
            conn = self._get_connection()
            with self._lock if self._is_memory_db else threading.Lock():
                cursor = conn.execute(
                    f"""
                    SELECT message_data FROM {self.messages_table} 
                    WHERE session_id = ? 
                    ORDER BY created_at ASC
                """,
                    (session_id,),
                )

                messages = []
                for (message_data,) in cursor.fetchall():
                    try:
                        message = json.loads(message_data)
                        messages.append(message)
                    except json.JSONDecodeError:
                        # Skip invalid JSON entries
                        continue

                return messages

        return await asyncio.to_thread(_get_messages_sync)

    async def add_messages(
        self, session_id: str, messages: list[TResponseInputItem]
    ) -> None:
        """Add new messages to the conversation history.

        Args:
            session_id: Unique identifier for the conversation session
            messages: List of input items to add to the history
        """
        if not messages:
            return

        def _add_messages_sync():
            conn = self._get_connection()

            with self._lock if self._is_memory_db else threading.Lock():
                # Ensure session exists
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO {self.sessions_table} (session_id) VALUES (?)
                """,
                    (session_id,),
                )

                # Add messages
                message_data = [
                    (session_id, json.dumps(message)) for message in messages
                ]
                conn.executemany(
                    f"""
                    INSERT INTO {self.messages_table} (session_id, message_data) VALUES (?, ?)
                """,
                    message_data,
                )

                # Update session timestamp
                conn.execute(
                    f"""
                    UPDATE {self.sessions_table} SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?
                """,
                    (session_id,),
                )

                conn.commit()

        await asyncio.to_thread(_add_messages_sync)

    async def pop_message(self, session_id: str) -> TResponseInputItem | None:
        """Remove and return the most recent message from the session.

        Args:
            session_id: Unique identifier for the conversation session

        Returns:
            The most recent message if it exists, None if the session is empty
        """

        def _pop_message_sync():
            conn = self._get_connection()
            with self._lock if self._is_memory_db else threading.Lock():
                cursor = conn.execute(
                    f"""
                    SELECT id, message_data FROM {self.messages_table} 
                    WHERE session_id = ? 
                    ORDER BY created_at DESC
                    LIMIT 1
                """,
                    (session_id,),
                )

                result = cursor.fetchone()
                if result:
                    message_id, message_data = result
                    try:
                        message = json.loads(message_data)
                        # Delete the message by ID
                        conn.execute(
                            f"""
                            DELETE FROM {self.messages_table} WHERE id = ?
                        """,
                            (message_id,),
                        )
                        conn.commit()
                        return message
                    except json.JSONDecodeError:
                        # Skip invalid JSON entries, but still delete the corrupted record
                        conn.execute(
                            f"""
                            DELETE FROM {self.messages_table} WHERE id = ?
                        """,
                            (message_id,),
                        )
                        conn.commit()
                        return None

                return None

        return await asyncio.to_thread(_pop_message_sync)

    async def clear_session(self, session_id: str) -> None:
        """Clear all messages for a given session.

        Args:
            session_id: Unique identifier for the conversation session
        """

        def _clear_session_sync():
            conn = self._get_connection()
            with self._lock if self._is_memory_db else threading.Lock():
                conn.execute(
                    f"DELETE FROM {self.messages_table} WHERE session_id = ?",
                    (session_id,),
                )
                conn.execute(
                    f"DELETE FROM {self.sessions_table} WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()

        await asyncio.to_thread(_clear_session_sync)

    def close(self) -> None:
        """Close the database connection."""
        if self._is_memory_db:
            if hasattr(self, "_shared_connection"):
                self._shared_connection.close()
        else:
            if hasattr(self._local, "connection"):
                self._local.connection.close()
