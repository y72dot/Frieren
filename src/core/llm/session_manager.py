"""Session lifecycle management with TTL, persistence, and message pruning."""

from __future__ import annotations

import json
import sqlite3
import time as _time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class Session:
    """A single LLM conversation session."""

    session_key: str
    messages: list[dict]            # OpenAI-format message list
    created_at: float = field(default_factory=_time.time)
    last_active: float = field(default_factory=_time.time)
    turn_count: int = 0
    summary: str = ""               # summary of pruned older messages
    summary_at_idx: int = 0         # messages before this index have been summarised


class SessionManager:
    """Manages LLM conversation sessions with TTL, persistence, and pruning.

    Replaces the module-level ``_session_cache`` dict in ``llm_core.py``.
    """

    def __init__(
        self,
        db_path: str = "data/llm_state.db",
        ttl: float = 3600.0,
        keep_recent_pairs: int = 3,
        max_context_tokens: int = 4096,
    ) -> None:
        self._cache: dict[str, Session] = {}
        self._ttl = ttl
        self._keep_recent_pairs = keep_recent_pairs
        self._max_context_tokens = max_context_tokens
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """Open (or create) the SQLite database and ensure schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                session_key TEXT PRIMARY KEY,
                messages_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                last_active REAL NOT NULL,
                turn_count INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                summary_at_idx INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._conn.commit()

    def recover(self) -> int:
        """Restore TTL-valid sessions from SQLite on startup. Returns count."""
        if self._conn is None:
            return 0
        now = _time.time()
        recovered = 0
        try:
            rows = self._conn.execute(
                "SELECT session_key, messages_json, created_at, last_active, "
                "turn_count, summary, summary_at_idx FROM sessions"
            ).fetchall()
        except sqlite3.OperationalError:
            return 0

        for row in rows:
            key, msgs_json, created, active, turns, summary, sa_idx = row
            if self._ttl > 0 and now - active >= self._ttl:
                continue  # expired
            try:
                messages = json.loads(msgs_json)
            except (json.JSONDecodeError, TypeError):
                messages = []
            session = Session(
                session_key=key,
                messages=messages,
                created_at=created,
                last_active=active,
                turn_count=turns,
                summary=summary,
                summary_at_idx=sa_idx,
            )
            self._cache[key] = session
            recovered += 1
        if recovered:
            logger.info(f"Recovered {recovered} session(s) from {self._db_path}")
        return recovered

    def shutdown(self) -> None:
        """Persist all in-memory sessions and close the database."""
        if self._conn is None:
            return
        for session in self._cache.values():
            self._save(session)
        self._conn.close()
        self._conn = None
        logger.info("SessionManager shut down")

    # ------------------------------------------------------------------
    # session access
    # ------------------------------------------------------------------

    def get_or_create(
        self,
        key: str,
        system_prompt: str,
        user_content: str,
    ) -> Session:
        """Return existing session if within TTL, otherwise create a new one."""
        now = _time.time()
        session = self._cache.get(key)

        if session is not None and (self._ttl <= 0 or now - session.last_active < self._ttl):
            # Reuse: append user message
            session.messages.append({"role": "user", "content": user_content})
            session.last_active = now
            logger.debug(f"Session {key} reused (msgs={len(session.messages)})")
            return session

        # New session
        session = Session(
            session_key=key,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        self._cache[key] = session
        logger.debug(f"Session {key} created [NEW]")
        return session

    def save(self, session: Session) -> None:
        """Persist a session to SQLite."""
        if session.session_key not in self._cache:
            self._cache[session.session_key] = session
        self._save(session)

    def remove(self, key: str) -> None:
        """Remove a session from cache and database."""
        self._cache.pop(key, None)
        if self._conn:
            self._conn.execute("DELETE FROM sessions WHERE session_key = ?", (key,))
            self._conn.commit()

    # ------------------------------------------------------------------
    # pruning
    # ------------------------------------------------------------------

    def prune(self, session: Session) -> Session:
        """Apply hybrid pruning: keep N recent pairs, summarise older messages.

        The summary is injected as an extra system-level note.
        """
        keep = self._keep_recent_pairs * 2  # user + assistant pairs
        if keep <= 0 or len(session.messages) <= keep + 2:
            return session

        # Messages structure: [system, user1, assistant1, user2, assistant2, ...]
        # Keep system message + last `keep` messages
        system_msg = session.messages[0]
        to_summarise = session.messages[1:-keep]
        kept = [system_msg] + session.messages[-keep:]

        summary_text = session.summary or ""
        if to_summarise:
            new_summary = self._summarise_messages(to_summarise)
            if summary_text:
                summary_text = summary_text + "\n" + new_summary
            else:
                summary_text = new_summary

        if summary_text:
            # Inject summary after system message
            summary_note = f"[之前的对话摘要]\n{summary_text}"
            kept.insert(1, {"role": "system", "content": summary_note})

        session.messages = kept
        session.summary = summary_text
        session.summary_at_idx = len(kept) - keep
        return session

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _save(self, session: Session) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_key, messages_json, created_at, last_active, turn_count, summary, summary_at_idx)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session.session_key,
                json.dumps(session.messages, ensure_ascii=False),
                session.created_at,
                session.last_active,
                session.turn_count,
                session.summary,
                session.summary_at_idx,
            ),
        )
        self._conn.commit()

    @staticmethod
    def _summarise_messages(messages: list[dict]) -> str:
        """Simple heuristic summary: concatenate user content snippets."""
        parts: list[str] = []
        for m in messages:
            if m.get("role") == "user":
                content = str(m.get("content", ""))[:200]
                if content:
                    parts.append(f"- 用户: {content}")
            elif m.get("role") == "assistant":
                content = str(m.get("content", ""))[:200]
                if content:
                    parts.append(f"- 助手: {content}")
        return "\n".join(parts) if parts else ""
