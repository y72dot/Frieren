"""Per-plugin key-value storage with permission checks and migration.

Each plugin gets an isolated key-value namespace backed by SQLite tables
in the bot's database.  Access is gated by ``storage.plugin.read`` and
``storage.plugin.write`` permissions declared in the plugin manifest.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from src.plugin.context import PermissionDeniedError

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS plugin_kv (
    plugin_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (plugin_id, key)
);

CREATE TABLE IF NOT EXISTS plugin_schema_versions (
    plugin_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# StorageMigration protocol
# ---------------------------------------------------------------------------


class StorageMigration(Protocol):
    """A migration function: ``(old_version, new_version, storage) -> None``."""

    async def __call__(
        self,
        old_version: int,
        new_version: int,
        storage: PluginStorage,
    ) -> None: ...


# ---------------------------------------------------------------------------
# PluginStorage
# ---------------------------------------------------------------------------


@dataclass
class PluginStorage:
    """Key-value store scoped to a single plugin.

    Permissions:
    - ``storage.plugin.read`` required for :meth:`get`, :meth:`get_json`, :meth:`list_keys`.
    - ``storage.plugin.write`` required for :meth:`set`, :meth:`set_json`, :meth:`delete`.
    - ``storage.plugin`` grants both read and write.
    """

    plugin_id: str
    permissions: list[str]
    connection: sqlite3.Connection

    _lock: asyncio.Lock | None = None

    @classmethod
    async def create(
        cls,
        plugin_id: str,
        permissions: list[str],
        db_path: str,
    ) -> PluginStorage:
        """Factory: create connection, init DDL, and return ready storage."""
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_DDL)
        conn.commit()
        storage = cls(
            plugin_id=plugin_id,
            permissions=permissions,
            connection=conn,
            _lock=asyncio.Lock(),
        )
        logger.debug(f"PluginStorage created for '{plugin_id}'")
        return storage

    # ------------------------------------------------------------------
    # permission checks
    # ------------------------------------------------------------------

    def _can(self, perm: str) -> bool:
        return perm in self.permissions or "plugin" in self.permissions

    def _check_read(self) -> None:
        if not self._can("plugin.read"):
            raise PermissionDeniedError(
                self.plugin_id, "storage.plugin.read",
                "plugin manifest must declare storage permission"
            )

    def _check_write(self) -> None:
        if not self._can("plugin.write"):
            raise PermissionDeniedError(
                self.plugin_id, "storage.plugin.write",
                "plugin manifest must declare storage permission"
            )

    # ------------------------------------------------------------------
    # basic KV API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> str | None:
        """Get a raw string value. Returns ``None`` if key does not exist."""
        self._check_read()
        async with self._lock:  # type: ignore[union-attr]
            row = self.connection.execute(
                "SELECT value_json FROM plugin_kv WHERE plugin_id=? AND key=?",
                (self.plugin_id, key),
            ).fetchone()
            if row is None:
                return None
            return row[0]

    async def set(self, key: str, value: str) -> None:
        """Store a raw string value, overwriting any existing entry."""
        self._check_write()
        async with self._lock:  # type: ignore[union-attr]
            self.connection.execute(
                """INSERT INTO plugin_kv (plugin_id, key, value_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(plugin_id, key) DO UPDATE SET
                   value_json=excluded.value_json,
                   updated_at=excluded.updated_at""",
                (self.plugin_id, key, value, time.time()),
            )
            self.connection.commit()

    async def delete(self, key: str) -> None:
        """Remove a key. No-op if the key does not exist."""
        self._check_write()
        async with self._lock:  # type: ignore[union-attr]
            self.connection.execute(
                "DELETE FROM plugin_kv WHERE plugin_id=? AND key=?",
                (self.plugin_id, key),
            )
            self.connection.commit()

    async def list_keys(self, prefix: str = "") -> list[str]:
        """Return all keys, optionally filtered by *prefix*."""
        self._check_read()
        async with self._lock:  # type: ignore[union-attr]
            if prefix:
                rows = self.connection.execute(
                    "SELECT key FROM plugin_kv WHERE plugin_id=? AND key LIKE ? "
                    "ORDER BY key",
                    (self.plugin_id, prefix + "%"),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT key FROM plugin_kv WHERE plugin_id=? ORDER BY key",
                    (self.plugin_id,),
                ).fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # JSON helpers
    # ------------------------------------------------------------------

    async def get_json(self, key: str) -> Any:
        """Get a JSON-deserialized value. Returns ``None`` for missing keys."""
        raw = await self.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(self, key: str, value: Any) -> None:
        """Store a value serialized as JSON."""
        await self.set(key, json.dumps(value, ensure_ascii=False, default=str))

    # ------------------------------------------------------------------
    # schema version
    # ------------------------------------------------------------------

    async def get_schema_version(self) -> int:
        """Return the current schema version for this plugin (0 if never set)."""
        self._check_read()
        async with self._lock:  # type: ignore[union-attr]
            row = self.connection.execute(
                "SELECT version FROM plugin_schema_versions WHERE plugin_id=?",
                (self.plugin_id,),
            ).fetchone()
            return int(row[0]) if row else 0

    async def set_schema_version(self, version: int) -> None:
        """Update the schema version for this plugin."""
        self._check_write()
        async with self._lock:  # type: ignore[union-attr]
            self.connection.execute(
                """INSERT INTO plugin_schema_versions (plugin_id, version)
                   VALUES (?, ?)
                   ON CONFLICT(plugin_id) DO UPDATE SET
                   version=excluded.version""",
                (self.plugin_id, version),
            )
            self.connection.commit()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        try:
            self.connection.close()
        except Exception:
            logger.opt(exception=True).debug(
                f"PluginStorage close error for '{self.plugin_id}'"
            )
