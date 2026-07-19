"""Message storage subsystem – persists and queries chat history via SQLite."""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.plugin.base import Event


@dataclass
class StoredMessage:
    """A single message record retrieved from the store."""

    message_id: int
    user_id: int
    nickname: str
    content: str
    time: int
    group_id: int | None = None


# ------------------------------------------------------------------
# helpers for extracting fields from raw napcat events
# ------------------------------------------------------------------


def _extract_nickname(raw: object, fallback_id: int) -> str:
    """Pull nickname from a napcat event object or dict."""
    if raw is None:
        return str(fallback_id)
    if isinstance(raw, dict):
        card = raw.get("sender", {}).get("card", "")
        nick = raw.get("sender", {}).get("nickname", "")
        return card or nick or str(fallback_id)
    # napcat-sdk typed event – try .sender.card / .sender.nickname
    sender = getattr(raw, "sender", None)
    if sender is not None:
        card = getattr(sender, "card", "") or ""
        nick = getattr(sender, "nickname", "") or ""
        return card or nick or str(fallback_id)
    return str(fallback_id)


def _extract_time(raw: object) -> int:
    """Extract message timestamp from a napcat event."""
    if isinstance(raw, dict):
        return int(raw.get("time", 0))
    return int(getattr(raw, "time", 0) or 0)


# ------------------------------------------------------------------
# MessageStore
# ------------------------------------------------------------------


class MessageStore:
    """SQLite-backed message history store.

    Thread-safe for concurrent reads/writes when using WAL mode.
    All methods are synchronous – SQLite queries are near-instant.
    """

    def __init__(
        self,
        db_path: str = "data/messages.db",
        max_per_group: int = 10000,
    ) -> None:
        self._max_per_group = max_per_group
        # Ensure data directory exists
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id  INTEGER PRIMARY KEY,
                group_id    INTEGER,
                user_id     INTEGER NOT NULL,
                nickname    TEXT NOT NULL DEFAULT '',
                content     TEXT NOT NULL DEFAULT '',
                time        INTEGER NOT NULL,
                is_group    INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_time ON messages(group_id, time DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_time ON messages(user_id, time DESC)"
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------

    def record(self, event: Event) -> None:
        """Persist an event as a message record (INSERT OR IGNORE for dedup)."""
        if event.message_id is None:
            return
        nickname = _extract_nickname(event.raw, event.user_id)
        msg_time = _extract_time(event.raw)
        self._conn.execute(
            "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,datetime('now'))",
            (
                event.message_id,
                event.group_id,
                event.user_id,
                nickname,
                event.message,
                msg_time,
                int(event.is_group),
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def recent(
        self, group_id: int, n: int = 10, exclude_user_id: int | None = None
    ) -> list[StoredMessage]:
        """Return the most recent *n* messages in a group, oldest first.

        If *exclude_user_id* is set, messages from that user are filtered out
        at the SQL level (useful for skipping bot's own messages).
        """
        if exclude_user_id is not None:
            rows = self._conn.execute(
                "SELECT message_id, user_id, nickname, content, time, group_id "
                "FROM messages WHERE group_id=? AND user_id!=? "
                "ORDER BY time DESC LIMIT ?",
                (group_id, exclude_user_id, n),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT message_id, user_id, nickname, content, time, group_id "
                "FROM messages WHERE group_id=? ORDER BY time DESC LIMIT ?",
                (group_id, n),
            ).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    def recent_private(self, user_id: int, n: int = 10) -> list[StoredMessage]:
        """Return the most recent *n* private messages with a user, oldest first."""
        rows = self._conn.execute(
            "SELECT message_id, user_id, nickname, content, time, group_id "
            "FROM messages WHERE is_group=0 AND user_id=? ORDER BY time DESC LIMIT ?",
            (user_id, n),
        ).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    def by_user(self, group_id: int, user_id: int, n: int = 10) -> list[StoredMessage]:
        """Return recent messages by a specific user in a group."""
        rows = self._conn.execute(
            "SELECT message_id, user_id, nickname, content, time, group_id "
            "FROM messages WHERE group_id=? AND user_id=? ORDER BY time DESC LIMIT ?",
            (group_id, user_id, n),
        ).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    def search(self, group_id: int, keyword: str, n: int = 20) -> list[StoredMessage]:
        """Full-text search for *keyword* in a group's messages."""
        rows = self._conn.execute(
            "SELECT message_id, user_id, nickname, content, time, group_id "
            "FROM messages WHERE group_id=? AND content LIKE ? "
            "ORDER BY time DESC LIMIT ?",
            (group_id, f"%{keyword}%", n),
        ).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------

    def trim(self, group_id: int) -> int:
        """Delete oldest messages when a group exceeds max_per_group. Returns deleted count."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE group_id=?", (group_id,)
        ).fetchone()
        count = row[0] if row else 0
        excess = count - self._max_per_group
        if excess > 0:
            self._conn.execute(
                "DELETE FROM messages WHERE group_id=? AND message_id IN "
                "(SELECT message_id FROM messages WHERE group_id=? "
                "ORDER BY time ASC LIMIT ?)",
                (group_id, group_id, excess),
            )
            self._conn.commit()
            logger.debug(f"Trimmed {excess} old messages from group {group_id}")
            return excess
        return 0

    def stats(self) -> dict:
        """Return basic statistics about the store."""
        total = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        groups = self._conn.execute(
            "SELECT COUNT(DISTINCT group_id) FROM messages WHERE is_group=1"
        ).fetchone()[0]
        return {"total_messages": total, "group_count": groups}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()
