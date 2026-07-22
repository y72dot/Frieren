"""Durable, lossless QQ event and message storage."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.adapters.qq import scan_cq, serialize_raw_event

if TYPE_CHECKING:
    from src.plugin.base import Event


@dataclass
class StoredMessage:
    """Backward-compatible message projection returned to plugins."""

    message_id: int
    user_id: int
    nickname: str
    content: str
    time: int
    group_id: int | None = None


@dataclass(frozen=True)
class StoredSegment:
    message_id: int
    segment_index: int
    segment_type: str
    raw_segment_json: str
    raw_cq: str | None = None


def _extract_nickname(raw: object, fallback_id: int) -> str:
    if raw is None:
        return str(fallback_id)
    if isinstance(raw, dict):
        card = raw.get("sender", {}).get("card", "")
        nick = raw.get("sender", {}).get("nickname", "")
        return card or nick or str(fallback_id)
    sender = getattr(raw, "sender", None)
    if sender is not None:
        card = getattr(sender, "card", "") or ""
        nick = getattr(sender, "nickname", "") or ""
        return card or nick or str(fallback_id)
    return str(fallback_id)


def _extract_time(raw: object) -> int:
    if isinstance(raw, dict):
        return int(raw.get("time", 0))
    return int(getattr(raw, "time", 0) or 0)


class MessageStore:
    """SQLite system of record for raw events and queryable messages.

    Raw events are committed to ``event_journal`` first. Message projection is
    a second idempotent transaction; only after it succeeds is the journal row
    marked projected. Existing query methods stay compatible with old plugins.
    """

    def __init__(
        self,
        db_path: str = "data/messages.db",
        max_per_group: int = 10000,
    ) -> None:
        self._max_per_group = max_per_group
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._fts_enabled = False
        self._create_tables()

    @property
    def connection(self) -> sqlite3.Connection:
        """Shared database connection for closely coupled projections."""
        return self._conn

    # ------------------------------------------------------------------
    # schema and migration
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY,
                group_id INTEGER,
                user_id INTEGER NOT NULL,
                nickname TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                time INTEGER NOT NULL,
                is_group INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                conversation_type TEXT NOT NULL DEFAULT 'group',
                conversation_id INTEGER NOT NULL DEFAULT 0,
                raw_message TEXT NOT NULL DEFAULT '',
                message_array_json TEXT NOT NULL DEFAULT '[]',
                raw_event_json TEXT NOT NULL DEFAULT '{}',
                search_text TEXT NOT NULL DEFAULT '',
                reply_to_message_id INTEGER,
                is_from_bot INTEGER NOT NULL DEFAULT 0,
                is_recalled INTEGER NOT NULL DEFAULT 0,
                ingestion_source TEXT NOT NULL DEFAULT 'legacy',
                first_seen_at INTEGER NOT NULL DEFAULT 0,
                last_synced_at INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._migrate_legacy_messages()
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS event_journal (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                received_at INTEGER NOT NULL,
                occurred_at INTEGER,
                raw_json TEXT NOT NULL,
                projected INTEGER NOT NULL DEFAULT 0,
                projection_error TEXT,
                trace_id TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS message_segments (
                message_id INTEGER NOT NULL,
                segment_index INTEGER NOT NULL,
                segment_type TEXT NOT NULL,
                raw_segment_json TEXT NOT NULL,
                raw_cq TEXT,
                artifact_id TEXT,
                PRIMARY KEY (message_id, segment_index),
                FOREIGN KEY (message_id) REFERENCES messages(message_id)
                    ON DELETE CASCADE
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS conversation_sync_state (
                conversation_type TEXT NOT NULL,
                conversation_id INTEGER NOT NULL,
                earliest_message_id INTEGER,
                latest_message_id INTEGER,
                earliest_time INTEGER,
                latest_time INTEGER,
                last_live_event_at INTEGER,
                last_backfill_at INTEGER,
                backfill_complete INTEGER NOT NULL DEFAULT 0,
                cursor_json TEXT,
                last_error TEXT,
                PRIMARY KEY (conversation_type, conversation_id)
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS sync_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_type TEXT NOT NULL,
                conversation_id INTEGER NOT NULL,
                gap_start INTEGER,
                gap_end INTEGER,
                reason TEXT NOT NULL,
                detected_at INTEGER NOT NULL,
                resolved_at INTEGER
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_time ON messages(group_id, time DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_time ON messages(user_id, time DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_time "
            "ON messages(conversation_type, conversation_id, time DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_projected "
            "ON event_journal(projected, received_at)"
        )
        self._create_fts()
        self._conn.commit()

    def _migrate_legacy_messages(self) -> None:
        additions = {
            "conversation_type": "TEXT NOT NULL DEFAULT 'group'",
            "conversation_id": "INTEGER NOT NULL DEFAULT 0",
            "raw_message": "TEXT NOT NULL DEFAULT ''",
            "message_array_json": "TEXT NOT NULL DEFAULT '[]'",
            "raw_event_json": "TEXT NOT NULL DEFAULT '{}'",
            "search_text": "TEXT NOT NULL DEFAULT ''",
            "reply_to_message_id": "INTEGER",
            "is_from_bot": "INTEGER NOT NULL DEFAULT 0",
            "is_recalled": "INTEGER NOT NULL DEFAULT 0",
            "ingestion_source": "TEXT NOT NULL DEFAULT 'legacy'",
            "first_seen_at": "INTEGER NOT NULL DEFAULT 0",
            "last_synced_at": "INTEGER NOT NULL DEFAULT 0",
        }
        existing = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(messages)")
        }
        for name, declaration in additions.items():
            if name not in existing:
                self._conn.execute(
                    f"ALTER TABLE messages ADD COLUMN {name} {declaration}"
                )
        now = int(_time.time())
        self._conn.execute(
            """UPDATE messages SET
                   conversation_type=CASE WHEN is_group=1 THEN 'group' ELSE 'private' END,
                   conversation_id=CASE WHEN is_group=1 THEN COALESCE(group_id, 0) ELSE user_id END,
                   raw_message=CASE WHEN raw_message='' THEN content ELSE raw_message END,
                   search_text=CASE WHEN search_text='' THEN content ELSE search_text END,
                   first_seen_at=CASE WHEN first_seen_at=0 THEN ? ELSE first_seen_at END,
                   last_synced_at=CASE WHEN last_synced_at=0 THEN ? ELSE last_synced_at END
               WHERE ingestion_source='legacy'""",
            (now, now),
        )

    def _create_fts(self) -> None:
        try:
            self._conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
                    message_id UNINDEXED,
                    search_text,
                    nickname
                )"""
            )
            self._fts_enabled = True
            count = self._conn.execute("SELECT COUNT(*) FROM message_fts").fetchone()[0]
            if count == 0:
                self._conn.execute(
                    """INSERT INTO message_fts(rowid, message_id, search_text, nickname)
                       SELECT message_id, message_id, search_text, nickname FROM messages"""
                )
        except sqlite3.OperationalError:
            logger.warning("SQLite FTS5 unavailable; message search will use LIKE")
            self._fts_enabled = False

    # ------------------------------------------------------------------
    # durable ingestion
    # ------------------------------------------------------------------

    def record_raw_event(
        self,
        raw_event: Any,
        *,
        event_type: str = "unhandled",
        source: str = "live",
        trace_id: str = "",
    ) -> str:
        raw_json = serialize_raw_event(raw_event)
        event_id = self._make_event_id(event_type, source, raw_json)
        now = int(_time.time())
        self._conn.execute(
            """INSERT OR IGNORE INTO event_journal
               (event_id, event_type, source, received_at, occurred_at,
                raw_json, projected, projection_error, trace_id)
               VALUES (?, ?, ?, ?, NULL, ?, 1, NULL, ?)""",
            (event_id, event_type, source, now, raw_json, trace_id),
        )
        self._conn.commit()
        return event_id

    def record(self, event: Event, *, trace_id: str = "") -> str:
        raw_json = event.raw_event_json or serialize_raw_event(event.raw)
        source = event.ingestion_source or "live"
        event_id = self._make_event_id(event.type, source, raw_json)
        received_at = int(_time.time())
        occurred_at = _extract_time(event.raw) or None

        # First commit the raw fact. A projection crash leaves a recoverable row.
        self._conn.execute(
            """INSERT OR IGNORE INTO event_journal
               (event_id, event_type, source, received_at, occurred_at,
                raw_json, projected, projection_error, trace_id)
               VALUES (?, ?, ?, ?, ?, ?, 0, NULL, ?)""",
            (
                event_id,
                event.type,
                source,
                received_at,
                occurred_at,
                raw_json,
                trace_id,
            ),
        )
        self._conn.commit()

        if event.message_id is None:
            self._mark_projected(event_id)
            return event_id

        try:
            self._conn.execute("BEGIN")
            self._project_message(event, raw_json, received_at)
            self._conn.execute(
                """UPDATE event_journal
                   SET projected=1, projection_error=NULL WHERE event_id=?""",
                (event_id,),
            )
            self._conn.commit()
        except Exception as exc:
            self._conn.rollback()
            self._conn.execute(
                "UPDATE event_journal SET projection_error=? WHERE event_id=?",
                (str(exc)[:1000], event_id),
            )
            self._conn.commit()
            raise
        return event_id

    def _project_message(self, event: Event, raw_json: str, now: int) -> None:
        assert event.message_id is not None
        nickname = _extract_nickname(event.raw, event.user_id)
        msg_time = _extract_time(event.raw)
        conversation_type = "group" if event.is_group else "private"
        conversation_id = (
            event.group_id if event.is_group else (event.peer_id or event.user_id)
        )
        conversation_id = int(conversation_id or 0)
        # EventBus sets raw_event_json even when NapCat raw_message is empty.
        # Hand-built legacy Events fall back to message for compatibility.
        raw_message = (
            event.raw_message
            if event.raw_event_json or event.message_array
            else (event.raw_message or event.message)
        )
        message_array_json = json.dumps(
            event.message_array, ensure_ascii=False, separators=(",", ":")
        )
        search_text = _build_search_text(raw_message, event.message_array, nickname)
        reply_to = _extract_reply_id(raw_message, event.message_array)
        is_from_bot = _is_from_bot(event.raw)

        self._conn.execute(
            """INSERT INTO messages (
                   message_id, group_id, user_id, nickname, content, time,
                   is_group, created_at, conversation_type, conversation_id,
                   raw_message, message_array_json, raw_event_json, search_text,
                   reply_to_message_id, is_from_bot, is_recalled,
                   ingestion_source, first_seen_at, last_synced_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET
                   group_id=COALESCE(messages.group_id, excluded.group_id),
                   nickname=CASE WHEN messages.nickname='' THEN excluded.nickname ELSE messages.nickname END,
                   content=CASE WHEN messages.content='' THEN excluded.content
                                ELSE messages.content END,
                   time=CASE WHEN messages.time=0 THEN excluded.time ELSE messages.time END,
                   raw_message=CASE WHEN messages.raw_message='' THEN excluded.raw_message
                                    ELSE messages.raw_message END,
                   message_array_json=CASE WHEN messages.message_array_json='[]'
                                           THEN excluded.message_array_json
                                           ELSE messages.message_array_json END,
                   raw_event_json=CASE WHEN messages.raw_event_json='{}'
                                       THEN excluded.raw_event_json
                                       ELSE messages.raw_event_json END,
                   search_text=CASE WHEN messages.search_text='' THEN excluded.search_text
                                    ELSE messages.search_text END,
                   reply_to_message_id=COALESCE(messages.reply_to_message_id,
                                                excluded.reply_to_message_id),
                   is_from_bot=MAX(messages.is_from_bot, excluded.is_from_bot),
                   ingestion_source=CASE WHEN messages.ingestion_source='live'
                                         THEN messages.ingestion_source
                                         ELSE excluded.ingestion_source END,
                   last_synced_at=excluded.last_synced_at""",
            (
                event.message_id,
                event.group_id,
                event.user_id,
                nickname,
                event.message,
                msg_time,
                int(event.is_group),
                conversation_type,
                conversation_id,
                raw_message,
                message_array_json,
                raw_json,
                search_text,
                reply_to,
                int(is_from_bot),
                event.ingestion_source or "live",
                now,
                now,
            ),
        )
        stored = self._conn.execute(
            """SELECT raw_message, message_array_json, search_text, nickname
               FROM messages WHERE message_id=?""",
            (event.message_id,),
        ).fetchone()
        stored_raw = str(stored[0])
        try:
            parsed_array = json.loads(stored[1])
            stored_array = parsed_array if isinstance(parsed_array, list) else []
        except (json.JSONDecodeError, TypeError):
            stored_array = []
        self._project_segments(event.message_id, stored_raw, stored_array)
        self._update_fts(event.message_id, str(stored[2]), str(stored[3]))
        self._update_sync_state(
            conversation_type,
            conversation_id,
            event.message_id,
            msg_time,
            source=event.ingestion_source or "live",
            now=now,
        )

    def _project_segments(
        self,
        message_id: int,
        raw_message: str,
        message_array: list[dict[str, Any]],
    ) -> None:
        self._conn.execute(
            "DELETE FROM message_segments WHERE message_id=?", (message_id,)
        )
        cq_refs = scan_cq(raw_message)
        if message_array:
            cq_by_type: dict[str, list[str]] = {}
            for ref in cq_refs:
                cq_by_type.setdefault(ref.type, []).append(ref.raw)
            for index, segment in enumerate(message_array):
                segment_type = str(segment.get("type", "unknown"))
                raw_cq = None
                candidates = cq_by_type.get(segment_type, [])
                if candidates:
                    raw_cq = candidates.pop(0)
                self._conn.execute(
                    """INSERT INTO message_segments
                       (message_id, segment_index, segment_type,
                        raw_segment_json, raw_cq, artifact_id)
                       VALUES (?, ?, ?, ?, ?, NULL)""",
                    (
                        message_id,
                        index,
                        segment_type,
                        json.dumps(segment, ensure_ascii=False, separators=(",", ":")),
                        raw_cq,
                    ),
                )
            return

        for index, ref in enumerate(cq_refs):
            raw_segment = {
                "type": ref.type,
                "data": ref.attributes,
                "derived_from_cq": True,
            }
            self._conn.execute(
                """INSERT INTO message_segments
                   (message_id, segment_index, segment_type,
                    raw_segment_json, raw_cq, artifact_id)
                   VALUES (?, ?, ?, ?, ?, NULL)""",
                (
                    message_id,
                    index,
                    ref.type,
                    json.dumps(raw_segment, ensure_ascii=False, separators=(",", ":")),
                    ref.raw,
                ),
            )

    def _update_fts(self, message_id: int, search_text: str, nickname: str) -> None:
        if not self._fts_enabled:
            return
        self._conn.execute("DELETE FROM message_fts WHERE rowid=?", (message_id,))
        self._conn.execute(
            """INSERT INTO message_fts(rowid, message_id, search_text, nickname)
               VALUES (?, ?, ?, ?)""",
            (message_id, message_id, search_text, nickname),
        )

    def _update_sync_state(
        self,
        conversation_type: str,
        conversation_id: int,
        message_id: int,
        msg_time: int,
        *,
        source: str,
        now: int,
    ) -> None:
        live_at = now if source == "live" else None
        backfill_at = now if source == "backfill" else None
        self._conn.execute(
            """INSERT INTO conversation_sync_state (
                   conversation_type, conversation_id,
                   earliest_message_id, latest_message_id,
                   earliest_time, latest_time,
                   last_live_event_at, last_backfill_at,
                   backfill_complete, cursor_json, last_error
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)
               ON CONFLICT(conversation_type, conversation_id) DO UPDATE SET
                   earliest_message_id=CASE
                       WHEN conversation_sync_state.earliest_message_id IS NULL
                            OR excluded.earliest_message_id < conversation_sync_state.earliest_message_id
                       THEN excluded.earliest_message_id
                       ELSE conversation_sync_state.earliest_message_id END,
                   latest_message_id=CASE
                       WHEN conversation_sync_state.latest_message_id IS NULL
                            OR excluded.latest_message_id > conversation_sync_state.latest_message_id
                       THEN excluded.latest_message_id
                       ELSE conversation_sync_state.latest_message_id END,
                   earliest_time=CASE
                       WHEN conversation_sync_state.earliest_time IS NULL
                            OR excluded.earliest_time < conversation_sync_state.earliest_time
                       THEN excluded.earliest_time ELSE conversation_sync_state.earliest_time END,
                   latest_time=CASE
                       WHEN conversation_sync_state.latest_time IS NULL
                            OR excluded.latest_time > conversation_sync_state.latest_time
                       THEN excluded.latest_time ELSE conversation_sync_state.latest_time END,
                   last_live_event_at=COALESCE(excluded.last_live_event_at,
                                               conversation_sync_state.last_live_event_at),
                   last_backfill_at=COALESCE(excluded.last_backfill_at,
                                             conversation_sync_state.last_backfill_at)""",
            (
                conversation_type,
                conversation_id,
                message_id,
                message_id,
                msg_time,
                msg_time,
                live_at,
                backfill_at,
            ),
        )

    def _mark_projected(self, event_id: str) -> None:
        self._conn.execute(
            "UPDATE event_journal SET projected=1, projection_error=NULL WHERE event_id=?",
            (event_id,),
        )
        self._conn.commit()

    @staticmethod
    def _make_event_id(event_type: str, source: str, raw_json: str) -> str:
        value = f"{source}\0{event_type}\0{raw_json}".encode()
        return hashlib.sha256(value).hexdigest()

    # ------------------------------------------------------------------
    # compatible writes
    # ------------------------------------------------------------------

    def record_bot_message(
        self,
        message_id: int,
        group_id: int | None,
        user_id: int,
        nickname: str,
        content: str,
        time: int,
        is_group: bool,
        peer_id: int | None = None,
    ) -> None:
        from src.plugin.base import Event

        raw = {
            "post_type": "message",
            "message_type": "group" if is_group else "private",
            "message_id": message_id,
            "group_id": group_id,
            "user_id": user_id,
            "time": time,
            "raw_message": content,
            "message": [{"type": "text", "data": {"text": content}}],
            "sender": {"user_id": user_id, "nickname": nickname},
            "self_id": user_id,
            "peer_id": peer_id,
        }
        self.record(
            Event(
                type="message.group" if is_group else "message.private",
                raw=raw,
                user_id=user_id,
                message_id=message_id,
                message=content,
                group_id=group_id,
                is_group=is_group,
                raw_message=content,
                message_array=raw["message"],
                raw_event_json=serialize_raw_event(raw),
                ingestion_source="live",
                peer_id=peer_id,
            )
        )

    # ------------------------------------------------------------------
    # compatible queries
    # ------------------------------------------------------------------

    def recent(
        self, group_id: int, n: int = 10, exclude_user_id: int | None = None
    ) -> list[StoredMessage]:
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
        rows = self._conn.execute(
            "SELECT message_id, user_id, nickname, content, time, group_id "
            "FROM messages WHERE is_group=0 AND (conversation_id=? OR user_id=?) "
            "ORDER BY time DESC LIMIT ?",
            (user_id, user_id, n),
        ).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    def by_user(self, group_id: int, user_id: int, n: int = 10) -> list[StoredMessage]:
        rows = self._conn.execute(
            "SELECT message_id, user_id, nickname, content, time, group_id "
            "FROM messages WHERE group_id=? AND user_id=? ORDER BY time DESC LIMIT ?",
            (group_id, user_id, n),
        ).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    def search(self, group_id: int, keyword: str, n: int = 20) -> list[StoredMessage]:
        fts_ids = self._fts_ids(keyword, n * 4)
        sql = (
            "SELECT message_id, user_id, nickname, content, time, group_id "
            "FROM messages WHERE group_id=?"
        )
        params: list[Any] = [group_id]
        if fts_ids:
            placeholders = ",".join("?" for _ in fts_ids)
            sql += f" AND (message_id IN ({placeholders}) OR content LIKE ?)"
            params.extend(fts_ids)
            params.append(f"%{keyword}%")
        else:
            sql += " AND content LIKE ?"
            params.append(f"%{keyword}%")
        sql += " ORDER BY time DESC LIMIT ?"
        params.append(n)
        rows = self._conn.execute(sql, params).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    def query(
        self,
        *,
        group_id: int | None = None,
        user_id: int | None = None,
        message_id: int | None = None,
        keyword: str | None = None,
        time_after: int | None = None,
        time_before: int | None = None,
        exclude_user_ids: list[int] | None = None,
        is_group: bool | None = None,
        conversation_type: str | None = None,
        conversation_id: int | None = None,
        n: int = 10,
    ) -> list[StoredMessage]:
        sql = (
            "SELECT message_id, user_id, nickname, content, time, group_id "
            "FROM messages WHERE 1=1"
        )
        params: list[Any] = []
        if group_id is not None:
            sql += " AND group_id=?"
            params.append(group_id)
        if message_id is not None:
            sql += " AND message_id=?"
            params.append(message_id)
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(user_id)
        if keyword is not None:
            sql += " AND (content LIKE ? OR search_text LIKE ?)"
            params.extend((f"%{keyword}%", f"%{keyword}%"))
        if time_after is not None:
            sql += " AND time >= ?"
            params.append(time_after)
        if time_before is not None:
            sql += " AND time <= ?"
            params.append(time_before)
        if exclude_user_ids:
            placeholders = ",".join("?" for _ in exclude_user_ids)
            sql += f" AND user_id NOT IN ({placeholders})"
            params.extend(exclude_user_ids)
        if is_group is not None:
            sql += " AND is_group=?"
            params.append(int(is_group))
        if conversation_type is not None:
            sql += " AND conversation_type=?"
            params.append(conversation_type)
        if conversation_id is not None:
            sql += " AND conversation_id=?"
            params.append(conversation_id)
        sql += " ORDER BY time DESC LIMIT ?"
        params.append(n)
        rows = self._conn.execute(sql, params).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    def _fts_ids(self, keyword: str, limit: int) -> list[int]:
        if not self._fts_enabled or not keyword.strip():
            return []
        phrase = '"' + keyword.replace('"', '""') + '"'
        try:
            rows = self._conn.execute(
                "SELECT message_id FROM message_fts WHERE message_fts MATCH ? LIMIT ?",
                (phrase, limit),
            ).fetchall()
            return [int(row[0]) for row in rows]
        except sqlite3.OperationalError:
            return []

    # ------------------------------------------------------------------
    # new read APIs
    # ------------------------------------------------------------------

    def get_message_record(self, message_id: int) -> dict[str, Any] | None:
        self._conn.row_factory = sqlite3.Row
        try:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE message_id=?", (message_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            self._conn.row_factory = None

    def get_segments(self, message_id: int) -> list[StoredSegment]:
        rows = self._conn.execute(
            """SELECT message_id, segment_index, segment_type,
                      raw_segment_json, raw_cq
               FROM message_segments WHERE message_id=? ORDER BY segment_index""",
            (message_id,),
        ).fetchall()
        return [StoredSegment(*row) for row in rows]

    def link_segment_artifact(
        self, message_id: int, segment_index: int, artifact_id: str
    ) -> None:
        self._conn.execute(
            "UPDATE message_segments SET artifact_id=? "
            "WHERE message_id=? AND segment_index=?",
            (artifact_id, message_id, segment_index),
        )
        self._conn.commit()

    def unprojected_events(self, limit: int = 100) -> list[dict[str, Any]]:
        self._conn.row_factory = sqlite3.Row
        try:
            rows = self._conn.execute(
                """SELECT * FROM event_journal WHERE projected=0
                   ORDER BY received_at LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._conn.row_factory = None

    def get_journal_event(self, event_id: str) -> dict[str, Any] | None:
        self._conn.row_factory = sqlite3.Row
        try:
            row = self._conn.execute(
                "SELECT * FROM event_journal WHERE event_id=?", (event_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            self._conn.row_factory = None

    def get_sync_state(
        self, conversation_type: str, conversation_id: int
    ) -> dict[str, Any] | None:
        self._conn.row_factory = sqlite3.Row
        try:
            row = self._conn.execute(
                """SELECT * FROM conversation_sync_state
                   WHERE conversation_type=? AND conversation_id=?""",
                (conversation_type, conversation_id),
            ).fetchone()
            return dict(row) if row else None
        finally:
            self._conn.row_factory = None

    def update_sync_status(
        self,
        conversation_type: str,
        conversation_id: int,
        *,
        cursor: dict[str, Any] | None = None,
        complete: bool | None = None,
        error: str | None = None,
    ) -> None:
        now = int(_time.time())
        self._conn.execute(
            """INSERT INTO conversation_sync_state (
                   conversation_type, conversation_id, last_backfill_at,
                   backfill_complete, cursor_json, last_error
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(conversation_type, conversation_id) DO UPDATE SET
                   last_backfill_at=excluded.last_backfill_at,
                   backfill_complete=CASE WHEN ? IS NULL
                       THEN conversation_sync_state.backfill_complete ELSE ? END,
                   cursor_json=COALESCE(excluded.cursor_json,
                                        conversation_sync_state.cursor_json),
                   last_error=excluded.last_error""",
            (
                conversation_type,
                conversation_id,
                now,
                int(bool(complete)),
                json.dumps(cursor, ensure_ascii=False) if cursor is not None else None,
                error,
                complete,
                int(bool(complete)),
            ),
        )
        self._conn.commit()

    def record_sync_gap(
        self,
        conversation_type: str,
        conversation_id: int,
        reason: str,
        *,
        gap_start: int | None = None,
        gap_end: int | None = None,
    ) -> int:
        existing = self._conn.execute(
            """SELECT id FROM sync_gaps WHERE conversation_type=?
               AND conversation_id=? AND reason=? AND resolved_at IS NULL
               AND gap_start IS ? AND gap_end IS ? LIMIT 1""",
            (conversation_type, conversation_id, reason, gap_start, gap_end),
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = self._conn.execute(
            """INSERT INTO sync_gaps (
                   conversation_type, conversation_id, gap_start, gap_end,
                   reason, detected_at, resolved_at
               ) VALUES (?, ?, ?, ?, ?, ?, NULL)""",
            (
                conversation_type,
                conversation_id,
                gap_start,
                gap_end,
                reason,
                int(_time.time()),
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def list_sync_gaps(
        self, conversation_type: str, conversation_id: int
    ) -> list[dict[str, Any]]:
        self._conn.row_factory = sqlite3.Row
        try:
            rows = self._conn.execute(
                """SELECT * FROM sync_gaps WHERE conversation_type=?
                   AND conversation_id=? AND resolved_at IS NULL
                   ORDER BY detected_at""",
                (conversation_type, conversation_id),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._conn.row_factory = None

    def resolve_sync_gaps(self, conversation_type: str, conversation_id: int) -> int:
        cursor = self._conn.execute(
            """UPDATE sync_gaps SET resolved_at=? WHERE conversation_type=?
               AND conversation_id=? AND resolved_at IS NULL""",
            (int(_time.time()), conversation_type, conversation_id),
        )
        self._conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------

    def trim(self, group_id: int) -> int:
        rows = self._conn.execute(
            "SELECT message_id FROM messages WHERE group_id=? ORDER BY time ASC",
            (group_id,),
        ).fetchall()
        excess = len(rows) - self._max_per_group
        if excess <= 0:
            return 0
        ids = [int(row[0]) for row in rows[:excess]]
        placeholders = ",".join("?" for _ in ids)
        self._conn.execute(
            f"DELETE FROM message_segments WHERE message_id IN ({placeholders})", ids
        )
        if self._fts_enabled:
            for message_id in ids:
                self._conn.execute(
                    "DELETE FROM message_fts WHERE rowid=?", (message_id,)
                )
        self._conn.execute(
            f"DELETE FROM messages WHERE message_id IN ({placeholders})", ids
        )
        self._conn.commit()
        logger.debug(f"Trimmed {excess} old messages from group {group_id}")
        return excess

    def stats(self) -> dict[str, int]:
        total = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        groups = self._conn.execute(
            "SELECT COUNT(DISTINCT group_id) FROM messages WHERE is_group=1"
        ).fetchone()[0]
        journal = self._conn.execute("SELECT COUNT(*) FROM event_journal").fetchone()[0]
        unprojected = self._conn.execute(
            "SELECT COUNT(*) FROM event_journal WHERE projected=0"
        ).fetchone()[0]
        return {
            "total_messages": total,
            "group_count": groups,
            "journal_events": journal,
            "unprojected_events": unprojected,
        }

    def close(self) -> None:
        if getattr(self, "_conn", None) is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


def _extract_reply_id(
    raw_message: str, message_array: list[dict[str, Any]]
) -> int | None:
    for segment in message_array:
        if segment.get("type") == "reply":
            value = segment.get("data", {}).get("id")
            with contextlib.suppress(TypeError, ValueError):
                return int(value)
    for ref in scan_cq(raw_message):
        if ref.type == "reply" and "id" in ref.attributes:
            with contextlib.suppress(ValueError):
                return int(ref.attributes["id"])
    return None


def _build_search_text(
    raw_message: str,
    message_array: list[dict[str, Any]],
    nickname: str,
) -> str:
    values = [raw_message, nickname]
    for segment in message_array:
        data = segment.get("data", {})
        if not isinstance(data, dict):
            continue
        for key in ("text", "file", "name", "summary", "content"):
            value = data.get(key)
            if value:
                values.append(str(value))
    return "\n".join(dict.fromkeys(value for value in values if value))


def _is_from_bot(raw: Any) -> bool:
    if isinstance(raw, dict):
        sender = raw.get("user_id")
        self_id = raw.get("self_id")
    else:
        sender = getattr(raw, "user_id", None)
        self_id = getattr(raw, "self_id", None)
    return sender is not None and self_id is not None and str(sender) == str(self_id)
