"""Unified configuration facade with redacted, versioned snapshots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from src.core.config import BotConfig

_REDACTED = "***REDACTED***"
_SENSITIVE_KEYS = {"api_key", "token", "password", "secret", "access_token"}


@dataclass(frozen=True)
class ConfigSnapshot:
    snapshot_id: str
    settings_version: int
    prompt_version: str
    effective_config_json: str
    prompt_hash: str
    created_at: int
    context_key: str = ""


class ConfigCenter:
    """Single read point for effective configuration.

    The typed :class:`BotConfig` remains the compatibility model during the
    migration. This facade adds path-based reads and durable, redacted
    snapshots without exposing secrets to the snapshot database.
    """

    def __init__(
        self,
        config: BotConfig,
        *,
        db_path: str | None = None,
    ) -> None:
        self._config = config
        self._settings_version = 1
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._memory_snapshots: dict[str, ConfigSnapshot] = {}
        if db_path is not None:
            self.init_db(db_path)

    @property
    def config(self) -> BotConfig:
        return self._config

    @property
    def settings_version(self) -> int:
        return self._settings_version

    @property
    def persistent(self) -> bool:
        return self._conn is not None

    def replace_config(
        self, config: BotConfig, *, changes: dict[str, Any] | None = None
    ) -> None:
        """Replace the effective typed config and advance its version."""
        if changes:
            sensitive = [path for path in changes if _is_sensitive_path(path)]
            if sensitive:
                raise PermissionError(
                    f"sensitive runtime settings cannot be persisted: {', '.join(sensitive)}"
                )
        self._config = config
        self._settings_version += 1
        if changes and self._conn is not None:
            now = int(time.time())
            self._conn.executemany(
                """INSERT INTO runtime_settings(path, value_json, updated_at)
                   VALUES (?, ?, ?) ON CONFLICT(path) DO UPDATE SET
                   value_json=excluded.value_json, updated_at=excluded.updated_at""",
                [
                    (path, json.dumps(value, ensure_ascii=False), now)
                    for path, value in changes.items()
                ],
            )
            self._conn.commit()
        self._record_settings_version()

    def get(
        self,
        path: str,
        default: Any = None,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> Any:
        """Read a dotted path, with optional task-local overrides."""
        if overrides and path in overrides:
            return overrides[path]
        value: Any = self._config
        for part in path.split("."):
            if isinstance(value, dict):
                if part not in value:
                    return default
                value = value[part]
            else:
                if not hasattr(value, part):
                    return default
                value = getattr(value, part)
        return value

    def init_db(self, db_path: str | None = None) -> None:
        if self._conn is not None:
            return
        resolved = db_path or self._db_path
        if resolved is None:
            return
        if resolved != ":memory:":
            path = Path(resolved)
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(resolved)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS settings_versions (
                version INTEGER PRIMARY KEY,
                content_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active'
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS runtime_settings (
                path TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS config_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                settings_version INTEGER NOT NULL,
                prompt_version TEXT NOT NULL,
                effective_config_json TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                context_key TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_config_snapshots_context "
            "ON config_snapshots(context_key, created_at DESC)"
        )
        self._conn.commit()
        row = self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM settings_versions").fetchone()
        self._settings_version = max(self._settings_version, int(row[0] or 0) + 1)
        for path, value_json in self._conn.execute(
            "SELECT path, value_json FROM runtime_settings ORDER BY path"
        ):
            _set_config_path(self._config, path, json.loads(value_json))
        self._record_settings_version()

    def create_snapshot(
        self,
        *,
        prompt_version: str,
        prompt_text: str,
        context_key: str = "",
    ) -> ConfigSnapshot:
        created_at = int(time.time())
        snapshot = ConfigSnapshot(
            snapshot_id=uuid.uuid4().hex,
            settings_version=self._settings_version,
            prompt_version=prompt_version,
            effective_config_json=self._redacted_config_json(),
            prompt_hash=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            created_at=created_at,
            context_key=context_key,
        )
        self._memory_snapshots[snapshot.snapshot_id] = snapshot
        if self._conn is not None:
            self._conn.execute(
                """INSERT INTO config_snapshots
                   (snapshot_id, settings_version, prompt_version,
                    effective_config_json, prompt_hash, context_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.snapshot_id,
                    snapshot.settings_version,
                    snapshot.prompt_version,
                    snapshot.effective_config_json,
                    snapshot.prompt_hash,
                    snapshot.context_key,
                    snapshot.created_at,
                ),
            )
            self._conn.commit()
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> ConfigSnapshot | None:
        cached = self._memory_snapshots.get(snapshot_id)
        if cached is not None:
            return cached
        if self._conn is None:
            return None
        row = self._conn.execute(
            """SELECT snapshot_id, settings_version, prompt_version,
                      effective_config_json, prompt_hash, created_at, context_key
               FROM config_snapshots WHERE snapshot_id=?""",
            (snapshot_id,),
        ).fetchone()
        return ConfigSnapshot(*row) if row else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _record_settings_version(self) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """INSERT OR IGNORE INTO settings_versions
               (version, content_json, created_at, reason, status)
               VALUES (?, ?, ?, ?, 'active')""",
            (
                self._settings_version,
                self._redacted_config_json(),
                int(time.time()),
                "configuration loaded",
            ),
        )
        self._conn.commit()

    def _redacted_config_json(self) -> str:
        raw = asdict(self._config) if is_dataclass(self._config) else deepcopy(self._config)
        redacted = _redact(raw)
        return json.dumps(redacted, ensure_ascii=False, sort_keys=True)


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if lowered == "env":
        return {"values": _REDACTED}
    if lowered in _SENSITIVE_KEYS or any(part in lowered for part in ("password", "secret")):
        return _REDACTED if value else ""
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, tuple):
        return [_redact(v) for v in value]
    return value


def _set_config_path(root: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    target = root
    for part in parts[:-1]:
        target = target[part] if isinstance(target, dict) else getattr(target, part)
    if isinstance(target, dict):
        target[parts[-1]] = value
    else:
        setattr(target, parts[-1], value)


def _is_sensitive_path(path: str) -> bool:
    parts = {part.lower() for part in path.split(".")}
    return bool(parts & _SENSITIVE_KEYS) or "env" in parts or "admin_users" in parts
