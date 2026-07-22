from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
ACTIVE_STATUSES = {
    "CREATED",
    "PLANNING",
    "RUNNING",
    "WAITING_TOOL",
    "WAITING_ARTIFACT",
    "WAITING_APPROVAL",
    "WAITING_USER",
    "SCHEDULED",
}


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    goal: str
    status: str
    trigger_type: str
    trigger_event_id: str | None
    conversation_type: str | None
    conversation_id: int | None
    requested_by: int | None
    created_at: int
    updated_at: int
    completed_at: int | None
    error: str | None
    metadata_json: str

    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)


@dataclass(frozen=True)
class TaskRunRecord:
    run_id: str
    task_id: str
    attempt: int
    status: str
    config_snapshot_id: str
    prompt_version: str
    started_at: int | None
    ended_at: int | None
    resume_token: str | None
    error: str | None


@dataclass(frozen=True)
class RunStepRecord:
    step_id: str
    run_id: str
    position: int
    kind: str
    status: str
    input_json: str | None
    output_json: str | None
    started_at: int | None
    ended_at: int | None
    error: str | None

    def input(self) -> Any:
        return json.loads(self.input_json) if self.input_json else None

    def output(self) -> Any:
        return json.loads(self.output_json) if self.output_json else None


class RuntimeStore:
    """SQLite system of record for durable tasks, runs and steps."""

    _TASK_COLUMNS = (
        "task_id, goal, status, trigger_type, trigger_event_id, "
        "conversation_type, conversation_id, requested_by, created_at, "
        "updated_at, completed_at, error, metadata_json"
    )
    _RUN_COLUMNS = (
        "run_id, task_id, attempt, status, config_snapshot_id, prompt_version, "
        "started_at, ended_at, resume_token, error"
    )
    _STEP_COLUMNS = (
        "step_id, run_id, position, kind, status, input_json, output_json, "
        "started_at, ended_at, error"
    )

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                trigger_event_id TEXT,
                conversation_type TEXT,
                conversation_id INTEGER,
                requested_by INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                completed_at INTEGER,
                error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS task_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                config_snapshot_id TEXT NOT NULL DEFAULT '',
                prompt_version TEXT NOT NULL DEFAULT '',
                started_at INTEGER,
                ended_at INTEGER,
                resume_token TEXT,
                error TEXT,
                UNIQUE(task_id, attempt)
            );
            CREATE TABLE IF NOT EXISTS run_steps (
                step_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                input_json TEXT,
                output_json TEXT,
                started_at INTEGER,
                ended_at INTEGER,
                error TEXT,
                UNIQUE(run_id, position)
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_runs_status ON task_runs(status, started_at);
            CREATE INDEX IF NOT EXISTS idx_steps_run ON run_steps(run_id, position);
            """
        )
        self.connection.commit()

    def create_task(
        self,
        goal: str,
        *,
        trigger_type: str,
        trigger_event_id: str | None = None,
        conversation_type: str | None = None,
        conversation_id: int | None = None,
        requested_by: int | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "CREATED",
        task_id: str | None = None,
    ) -> TaskRecord:
        now = int(time.time())
        task_id = task_id or uuid.uuid4().hex
        self.connection.execute(
            """INSERT INTO tasks (
                   task_id, goal, status, trigger_type, trigger_event_id,
                   conversation_type, conversation_id, requested_by, created_at,
                   updated_at, completed_at, error, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (
                task_id,
                goal,
                status,
                trigger_type,
                trigger_event_id,
                conversation_type,
                conversation_id,
                requested_by,
                now,
                now,
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        result = self.get_task(task_id)
        assert result is not None
        return result

    def update_task(
        self, task_id: str, status: str, *, error: str | None = None
    ) -> None:
        now = int(time.time())
        self.connection.execute(
            """UPDATE tasks SET status=?, updated_at=?, error=?,
                   completed_at=CASE WHEN ? THEN ? ELSE completed_at END
               WHERE task_id=?""",
            (status, now, _error(error), int(status in TERMINAL_STATUSES), now, task_id),
        )
        self.connection.commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        row = self.connection.execute(
            f"SELECT {self._TASK_COLUMNS} FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        return TaskRecord(*row) if row else None

    def create_run(
        self,
        task_id: str,
        *,
        status: str = "CREATED",
        config_snapshot_id: str = "",
        prompt_version: str = "",
        resume_token: str | None = None,
        run_id: str | None = None,
    ) -> TaskRunRecord:
        run_id = run_id or uuid.uuid4().hex
        attempt = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(attempt), 0) + 1 FROM task_runs WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
        )
        now = int(time.time()) if status == "RUNNING" else None
        self.connection.execute(
            """INSERT INTO task_runs (
                   run_id, task_id, attempt, status, config_snapshot_id,
                   prompt_version, started_at, ended_at, resume_token, error
               ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)""",
            (
                run_id,
                task_id,
                attempt,
                status,
                config_snapshot_id,
                prompt_version,
                now,
                resume_token,
            ),
        )
        self.connection.commit()
        result = self.get_run(run_id)
        assert result is not None
        return result

    def update_run(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
        resume_token: str | None = None,
    ) -> None:
        now = int(time.time())
        self.connection.execute(
            """UPDATE task_runs SET status=?, error=?,
                   resume_token=COALESCE(?, resume_token),
                   started_at=CASE WHEN started_at IS NULL AND ?='RUNNING'
                                   THEN ? ELSE started_at END,
                   ended_at=CASE WHEN ? THEN ? ELSE ended_at END
               WHERE run_id=?""",
            (
                status,
                _error(error),
                resume_token,
                status,
                now,
                int(status in TERMINAL_STATUSES),
                now,
                run_id,
            ),
        )
        self.connection.commit()

    def get_run(self, run_id: str) -> TaskRunRecord | None:
        row = self.connection.execute(
            f"SELECT {self._RUN_COLUMNS} FROM task_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return TaskRunRecord(*row) if row else None

    def create_step(
        self,
        run_id: str,
        kind: str,
        *,
        input_data: Any = None,
        status: str = "CREATED",
        step_id: str | None = None,
    ) -> RunStepRecord:
        step_id = step_id or uuid.uuid4().hex
        position = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM run_steps WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
        )
        now = int(time.time()) if status == "RUNNING" else None
        self.connection.execute(
            """INSERT INTO run_steps (
                   step_id, run_id, position, kind, status, input_json,
                   output_json, started_at, ended_at, error
               ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL)""",
            (step_id, run_id, position, kind, status, _json(input_data), now),
        )
        self.connection.commit()
        result = self.get_step(step_id)
        assert result is not None
        return result

    def update_step(
        self,
        step_id: str,
        status: str,
        *,
        output: Any = None,
        error: str | None = None,
    ) -> None:
        now = int(time.time())
        self.connection.execute(
            """UPDATE run_steps SET status=?, output_json=COALESCE(?, output_json),
                   error=?, started_at=CASE WHEN started_at IS NULL AND ?='RUNNING'
                                            THEN ? ELSE started_at END,
                   ended_at=CASE WHEN ? THEN ? ELSE ended_at END
               WHERE step_id=?""",
            (
                status,
                _json(output) if output is not None else None,
                _error(error),
                status,
                now,
                int(status in TERMINAL_STATUSES),
                now,
                step_id,
            ),
        )
        self.connection.commit()

    def get_step(self, step_id: str) -> RunStepRecord | None:
        row = self.connection.execute(
            f"SELECT {self._STEP_COLUMNS} FROM run_steps WHERE step_id=?", (step_id,)
        ).fetchone()
        return RunStepRecord(*row) if row else None

    def list_steps(self, run_id: str) -> list[RunStepRecord]:
        rows = self.connection.execute(
            f"SELECT {self._STEP_COLUMNS} FROM run_steps WHERE run_id=? ORDER BY position",
            (run_id,),
        ).fetchall()
        return [RunStepRecord(*row) for row in rows]

    def list_active_runs(self) -> list[TaskRunRecord]:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        rows = self.connection.execute(
            f"SELECT {self._RUN_COLUMNS} FROM task_runs WHERE status IN ({placeholders})",
            tuple(sorted(ACTIVE_STATUSES)),
        ).fetchall()
        return [TaskRunRecord(*row) for row in rows]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _error(value: str | None) -> str | None:
    return value[:4000] if value else None
