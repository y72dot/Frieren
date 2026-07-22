from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolInvocation:
    invocation_id: str
    run_id: str
    task_id: str | None
    step_id: str | None
    tool_name: str
    tool_version: str
    arguments_json: str
    status: str
    idempotency_key: str | None
    result_json: str | None
    error: str | None
    started_at: int | None
    ended_at: int | None
    trace_id: str
    user_id: int | None
    group_id: int | None
    config_snapshot_id: str

    def result(self) -> Any:
        return json.loads(self.result_json) if self.result_json else None


class InvocationStore:
    """Durable lifecycle records for every attempted tool invocation."""

    _COLUMNS = (
        "invocation_id, run_id, task_id, step_id, tool_name, tool_version, "
        "arguments_json, status, idempotency_key, result_json, error, "
        "started_at, ended_at, trace_id, user_id, group_id, config_snapshot_id"
    )

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._create_table()

    def _create_table(self) -> None:
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS tool_invocations (
                invocation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT,
                step_id TEXT,
                tool_name TEXT NOT NULL,
                tool_version TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT,
                result_json TEXT,
                error TEXT,
                started_at INTEGER,
                ended_at INTEGER,
                trace_id TEXT NOT NULL DEFAULT '',
                user_id INTEGER,
                group_id INTEGER,
                config_snapshot_id TEXT NOT NULL DEFAULT ''
            )"""
        )
        columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(tool_invocations)")
        }
        if "step_id" not in columns:
            self.connection.execute("ALTER TABLE tool_invocations ADD COLUMN step_id TEXT")
        # Failed attempts must be retryable with the same idempotency key.
        self.connection.execute("DROP INDEX IF EXISTS idx_tool_invocation_idempotency")
        self.connection.execute(
            """CREATE UNIQUE INDEX idx_tool_invocation_idempotency
               ON tool_invocations(tool_name, idempotency_key)
               WHERE idempotency_key IS NOT NULL AND status='succeeded'"""
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_invocation_run "
            "ON tool_invocations(run_id, started_at)"
        )
        self.connection.commit()

    def begin(
        self,
        *,
        tool_name: str,
        tool_version: str,
        arguments: dict[str, Any],
        run_id: str | None,
        task_id: str | None,
        invocation_id: str | None,
        idempotency_key: str | None,
        trace_id: str,
        user_id: int | None,
        group_id: int | None,
        config_snapshot_id: str,
        step_id: str | None = None,
    ) -> ToolInvocation:
        invocation_id = invocation_id or uuid.uuid4().hex
        run_id = run_id or f"adhoc:{invocation_id}"
        arguments_json = json.dumps(
            _redact(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.connection.execute(
            """INSERT INTO tool_invocations (
                   invocation_id, run_id, task_id, step_id, tool_name, tool_version,
                   arguments_json, status, idempotency_key, result_json,
                   error, started_at, ended_at, trace_id, user_id, group_id,
                   config_snapshot_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'validating', ?, NULL, NULL, ?, NULL,
                         ?, ?, ?, ?)""",
            (
                invocation_id,
                run_id,
                task_id,
                step_id,
                tool_name,
                tool_version,
                arguments_json,
                idempotency_key,
                int(time.time()),
                trace_id,
                user_id,
                group_id,
                config_snapshot_id,
            ),
        )
        self.connection.commit()
        result = self.get(invocation_id)
        assert result is not None
        return result

    def transition(
        self,
        invocation_id: str,
        status: str,
        *,
        result: Any = None,
        error: str | None = None,
        terminal: bool = False,
    ) -> None:
        result_json = (
            json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))
            if result is not None
            else None
        )
        self.connection.execute(
            """UPDATE tool_invocations SET status=?, result_json=COALESCE(?, result_json),
                   error=?, ended_at=CASE WHEN ? THEN ? ELSE ended_at END
               WHERE invocation_id=?""",
            (
                status,
                result_json,
                error[:2000] if error else None,
                int(terminal),
                int(time.time()),
                invocation_id,
            ),
        )
        self.connection.commit()

    def get(self, invocation_id: str) -> ToolInvocation | None:
        row = self.connection.execute(
            f"SELECT {self._COLUMNS} FROM tool_invocations WHERE invocation_id=?",
            (invocation_id,),
        ).fetchone()
        return ToolInvocation(*row) if row else None

    def find_succeeded(
        self, tool_name: str, idempotency_key: str
    ) -> ToolInvocation | None:
        row = self.connection.execute(
            f"SELECT {self._COLUMNS} FROM tool_invocations "
            "WHERE tool_name=? AND idempotency_key=? AND status='succeeded' LIMIT 1",
            (tool_name, idempotency_key),
        ).fetchone()
        return ToolInvocation(*row) if row else None

    def list_for_run(self, run_id: str) -> list[ToolInvocation]:
        rows = self.connection.execute(
            f"SELECT {self._COLUMNS} FROM tool_invocations "
            "WHERE run_id=? ORDER BY started_at, rowid",
            (run_id,),
        ).fetchall()
        return [ToolInvocation(*row) for row in rows]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***"
            if any(
                token in key.lower()
                for token in ("token", "secret", "password", "api_key")
            )
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
