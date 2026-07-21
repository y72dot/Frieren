"""Three-layer memory system: working, episodic, and semantic memory.

Uses the shared ``data/llm_state.db`` SQLite database (same file as
:class:`SessionManager`) for persistence.
"""

from __future__ import annotations

import json
import sqlite3
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class MemoryConfig:
    """Configuration for the memory subsystem."""

    episodic_enabled: bool = True
    episodic_max: int = 1000
    episodic_search_limit: int = 5
    semantic_enabled: bool = True
    consolidation_enabled: bool = True


class MemoryManager:
    """Manages three memory layers backed by SQLite.

    - **Working memory**: current ``Session.messages`` (in-memory, no DB).
    - **Episodic memory**: historical conversation summaries.
    - **Semantic memory**: extracted facts (subject-predicate-object).
    """

    def __init__(
        self,
        db_path: str = "data/llm_state.db",
        config: MemoryConfig | None = None,
    ) -> None:
        self.config = config or MemoryConfig()
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """Ensure the memory tables exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY,
                session_key TEXT NOT NULL,
                timestamp REAL NOT NULL,
                summary TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT '',
                UNIQUE(subject, predicate)
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_key, timestamp)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)"
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # episodic memory
    # ------------------------------------------------------------------

    def store_episode(self, session_key: str, summary: str, metadata: dict | None = None) -> int:
        """Persist a conversation summary. Returns the new row id."""
        if not self.config.episodic_enabled or self._conn is None:
            return -1
        cur = self._conn.execute(
            "INSERT INTO episodes (session_key, timestamp, summary, metadata) VALUES (?, ?, ?, ?)",
            (session_key, _time.time(), summary, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        self._conn.commit()
        self._enforce_episodic_limit()
        return cur.lastrowid

    def search_episodes(
        self,
        session_key: str | None = None,
        keyword: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search episodes by session_key and/or keyword. Returns newest first."""
        if not self.config.episodic_enabled or self._conn is None:
            return []
        limit = limit or self.config.episodic_search_limit
        query = "SELECT session_key, timestamp, summary, metadata FROM episodes WHERE 1=1"
        params: list = []
        if session_key:
            query += " AND session_key = ?"
            params.append(session_key)
        if keyword:
            query += " AND summary LIKE ?"
            params.append(f"%{keyword}%")
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "session_key": r[0],
                "timestamp": r[1],
                "summary": r[2],
                "metadata": json.loads(r[3]) if r[3] else {},
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # semantic memory (facts)
    # ------------------------------------------------------------------

    def store_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float = 1.0,
        source: str = "",
    ) -> None:
        """Upsert a fact triple."""
        if not self.config.semantic_enabled or self._conn is None:
            return
        self._conn.execute(
            """INSERT OR REPLACE INTO facts (subject, predicate, object, confidence, source)
               VALUES (?, ?, ?, ?, ?)""",
            (subject, predicate, obj, confidence, source),
        )
        self._conn.commit()

    def query_facts(self, subject: str, predicate: str | None = None) -> list[dict[str, Any]]:
        """Look up facts about a subject, optionally filtered by predicate."""
        if not self.config.semantic_enabled or self._conn is None:
            return []
        if predicate:
            rows = self._conn.execute(
                "SELECT subject, predicate, object, confidence, source FROM facts WHERE subject = ? AND predicate = ?",
                (subject, predicate),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT subject, predicate, object, confidence, source FROM facts WHERE subject = ?",
                (subject,),
            ).fetchall()
        return [
            {
                "subject": r[0],
                "predicate": r[1],
                "object": r[2],
                "confidence": r[3],
                "source": r[4],
            }
            for r in rows
        ]

    def query_all_facts(self, subject: str) -> list[dict[str, Any]]:
        """Convenience: get all facts about a subject."""
        return self.query_facts(subject)

    # ------------------------------------------------------------------
    # memory injection for LLM context
    # ------------------------------------------------------------------

    def inject_context(self, session_key: str, user_id: int | str) -> str:
        """Build a context prefix from episodic + semantic memory.

        Returns a string to prepend to the system message, or empty string.
        """
        parts: list[str] = []

        # Episodic: recent summaries for this session key
        if self.config.episodic_enabled:
            episodes = self.search_episodes(session_key=session_key, limit=3)
            if episodes:
                lines = ["[相关历史]"]
                for ep in episodes:
                    lines.append(f"- {ep['summary'][:300]}")
                parts.append("\n".join(lines))

        # Semantic: known facts about this user
        if self.config.semantic_enabled:
            facts = self.query_facts(str(user_id))
            if facts:
                lines = ["[已知偏好]"]
                for f in facts[:5]:
                    lines.append(f"- {f['subject']} {f['predicate']} {f['object']}")
                parts.append("\n".join(lines))

        return "\n".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # consolidation (called after session end)
    # ------------------------------------------------------------------

    async def consolidate_session(
        self,
        session_key: str,
        messages: list[dict],
        user_id: int | str,
        provider=None,           # optional: use LLM for better summaries
    ) -> None:
        """Post-session cleanup: summarise and extract facts."""
        if not self.config.consolidation_enabled:
            return

        # Store an episode summary
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if user_msgs:
            summary = f"对话共 {len(messages)} 条消息，用户 {user_id} 参与。"
            if len(user_msgs) <= 5:
                previews = [str(m.get("content", ""))[:100] for m in user_msgs]
                summary += " 内容：" + "; ".join(previews)
            self.store_episode(session_key, summary)

        # Extract simple facts: count tool usage etc.
        if self.config.semantic_enabled:
            tool_counts: dict[str, int] = {}
            for m in messages:
                if m.get("role") == "tool":
                    try:
                        content = json.loads(m.get("content", "{}"))
                        if isinstance(content, dict):
                            for k in content:
                                if k != "error":
                                    tool_counts[k] = tool_counts.get(k, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        pass

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _enforce_episodic_limit(self) -> None:
        """Delete oldest episodes if over the configured max."""
        if not self.config.episodic_enabled or self._conn is None:
            return
        count = self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
        if count and count[0] > self.config.episodic_max:
            excess = count[0] - self.config.episodic_max
            self._conn.execute(
                "DELETE FROM episodes WHERE id IN (SELECT id FROM episodes ORDER BY timestamp ASC LIMIT ?)",
                (excess,),
            )
            self._conn.commit()
