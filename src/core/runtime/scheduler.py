from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

from src.core.runtime.controller import DurableRuntime


@dataclass(frozen=True)
class ScheduleRecord:
    schedule_id: str
    name: str
    enabled: bool
    trigger_type: str
    trigger_spec_json: str
    timezone: str
    task_template_json: str
    target_conversation_type: str | None
    target_conversation_id: int | None
    created_by: int | None
    next_run_at: int | None
    last_run_at: int | None
    misfire_policy: str
    max_concurrency: int
    created_at: int
    updated_at: int
    plugin_id: str = ""
    plugin_generation: int = 0

    def trigger_spec(self) -> dict[str, Any]:
        return json.loads(self.trigger_spec_json)

    def task_template(self) -> dict[str, Any]:
        return json.loads(self.task_template_json)


class ScheduleStore:
    _COLUMNS = (
        "schedule_id, name, enabled, trigger_type, trigger_spec_json, timezone, "
        "task_template_json, target_conversation_type, target_conversation_id, "
        "created_by, next_run_at, last_run_at, misfire_policy, max_concurrency, "
        "created_at, updated_at, plugin_id, plugin_generation"
    )

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                schedule_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                trigger_type TEXT NOT NULL,
                trigger_spec_json TEXT NOT NULL,
                timezone TEXT NOT NULL,
                task_template_json TEXT NOT NULL,
                target_conversation_type TEXT,
                target_conversation_id INTEGER,
                created_by INTEGER,
                next_run_at INTEGER,
                last_run_at INTEGER,
                misfire_policy TEXT NOT NULL,
                max_concurrency INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_schedules_due
                ON schedules(enabled, next_run_at);
            """
        )
        # Migration: add plugin_id / plugin_generation columns if missing.
        import contextlib

        for col, col_def in [
            ("plugin_id", "TEXT NOT NULL DEFAULT ''"),
            ("plugin_generation", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            with contextlib.suppress(sqlite3.OperationalError):
                self.connection.execute(
                    f"ALTER TABLE schedules ADD COLUMN {col} {col_def}"
                )
        with contextlib.suppress(sqlite3.OperationalError):
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_schedules_plugin ON schedules(plugin_id)"
            )
        self.connection.commit()

    def create(
        self,
        *,
        name: str,
        trigger_type: str,
        trigger_spec: dict[str, Any],
        timezone: str,
        task_template: dict[str, Any],
        target_conversation_type: str | None = None,
        target_conversation_id: int | None = None,
        created_by: int | None = None,
        misfire_policy: str = "run_once",
        max_concurrency: int = 1,
        now: int | None = None,
        plugin_id: str = "",
        plugin_generation: int = 0,
    ) -> ScheduleRecord:
        now = int(time.time()) if now is None else int(now)
        _validate_schedule(trigger_type, trigger_spec, timezone, misfire_policy)
        if (
            misfire_policy == "catch_up"
            and task_template.get("kind") == "agent_prompt"
            and not task_template.get("allow_catch_up", False)
        ):
            raise ValueError("catch_up is disabled for message-producing agent prompts")
        schedule_id = uuid.uuid4().hex
        next_run = _initial_next_run(trigger_type, trigger_spec, timezone, now)
        self.connection.execute(
            """INSERT INTO schedules (
                   schedule_id, name, enabled, trigger_type, trigger_spec_json,
                   timezone, task_template_json, target_conversation_type,
                   target_conversation_id, created_by, next_run_at, last_run_at,
                   misfire_policy, max_concurrency, created_at, updated_at,
                   plugin_id, plugin_generation
               ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
            (
                schedule_id,
                name,
                trigger_type,
                _json(trigger_spec),
                timezone,
                _json(task_template),
                target_conversation_type,
                target_conversation_id,
                created_by,
                next_run,
                misfire_policy,
                max(1, int(max_concurrency)),
                now,
                now,
                plugin_id,
                plugin_generation,
            ),
        )
        self.connection.commit()
        result = self.get(schedule_id)
        assert result is not None
        return result

    def get(self, schedule_id: str) -> ScheduleRecord | None:
        row = self.connection.execute(
            f"SELECT {self._COLUMNS} FROM schedules WHERE schedule_id=?",
            (schedule_id,),
        ).fetchone()
        return _schedule_from_row(row) if row else None

    def list(self, *, enabled: bool | None = None) -> list[ScheduleRecord]:
        sql = f"SELECT {self._COLUMNS} FROM schedules"
        params: tuple[Any, ...] = ()
        if enabled is not None:
            sql += " WHERE enabled=?"
            params = (int(enabled),)
        sql += " ORDER BY created_at, schedule_id"
        return [_schedule_from_row(row) for row in self.connection.execute(sql, params)]

    def due(self, now: int) -> list[ScheduleRecord]:
        rows = self.connection.execute(
            f"SELECT {self._COLUMNS} FROM schedules "
            "WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=? "
            "ORDER BY next_run_at, schedule_id",
            (int(now),),
        ).fetchall()
        return [_schedule_from_row(row) for row in rows]

    def set_enabled(self, schedule_id: str, enabled: bool, *, now: int | None = None) -> None:
        now = int(time.time()) if now is None else int(now)
        record = self.get(schedule_id)
        if record is None:
            raise KeyError(f"schedule not found: {schedule_id}")
        next_run = record.next_run_at
        if enabled and next_run is None and record.trigger_type != "event":
            next_run = _initial_next_run(
                record.trigger_type, record.trigger_spec(), record.timezone, now
            )
        self.connection.execute(
            "UPDATE schedules SET enabled=?, next_run_at=?, updated_at=? WHERE schedule_id=?",
            (int(enabled), next_run, now, schedule_id),
        )
        self.connection.commit()

    def advance(
        self,
        schedule_id: str,
        *,
        next_run_at: int | None,
        last_run_at: int | None,
        enabled: bool,
        now: int,
    ) -> None:
        self.connection.execute(
            """UPDATE schedules SET next_run_at=?, last_run_at=COALESCE(?, last_run_at),
                   enabled=?, updated_at=? WHERE schedule_id=?""",
            (next_run_at, last_run_at, int(enabled), now, schedule_id),
        )
        self.connection.commit()

    def delete(self, schedule_id: str) -> None:
        self.connection.execute("DELETE FROM schedules WHERE schedule_id=?", (schedule_id,))
        self.connection.commit()

    def list_by_plugin(self, plugin_id: str) -> list[ScheduleRecord]:
        """Return all schedules owned by *plugin_id*."""
        rows = self.connection.execute(
            f"SELECT {self._COLUMNS} FROM schedules WHERE plugin_id=? "
            "ORDER BY created_at, schedule_id",
            (plugin_id,),
        ).fetchall()
        return [_schedule_from_row(row) for row in rows]

    def cancel_by_plugin(self, plugin_id: str) -> int:
        """Delete all schedules owned by *plugin_id*. Returns count of deleted."""
        c = self.connection.execute(
            "DELETE FROM schedules WHERE plugin_id=?", (plugin_id,)
        )
        self.connection.commit()
        return c.rowcount


class SchedulerService:
    """Persistent scheduler; every occurrence creates a durable TaskRun."""

    def __init__(
        self,
        store: ScheduleStore,
        runtime: DurableRuntime,
        *,
        poll_interval: float = 1.0,
        max_catch_up: int = 10,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.poll_interval = poll_interval
        self.max_catch_up = max(1, max_catch_up)
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None

    def create(self, **kwargs: Any) -> ScheduleRecord:
        return self.store.create(**kwargs)

    async def tick(self, now: int | None = None, *, recovering: bool = False) -> list[str]:
        now = int(time.time()) if now is None else int(now)
        created_runs: list[str] = []
        for schedule in self.store.due(now):
            occurrences, next_run, enabled = _due_occurrences(
                schedule, now, recovering=recovering, limit=self.max_catch_up
            )
            if self._active_count(schedule.schedule_id) >= schedule.max_concurrency:
                continue
            for _occurrence in occurrences:
                task, run = self.runtime.create_task_run(
                    goal=str(schedule.task_template().get("goal", schedule.name)),
                    trigger_type="scheduled",
                    trigger_event_id=schedule.schedule_id,
                    template=schedule.task_template(),
                    requested_by=schedule.created_by,
                    conversation_type=schedule.target_conversation_type,
                    conversation_id=schedule.target_conversation_id,
                    scheduled=True,
                )
                del task
                self.runtime.submit(run.run_id)
                created_runs.append(run.run_id)
                if self._active_count(schedule.schedule_id) >= schedule.max_concurrency:
                    break
            self.store.advance(
                schedule.schedule_id,
                next_run_at=next_run,
                last_run_at=occurrences[-1] if occurrences else None,
                enabled=enabled,
                now=now,
            )
        return created_runs

    async def trigger_event(self, event_name: str, payload: dict[str, Any]) -> list[str]:
        created: list[str] = []
        for schedule in self.store.list(enabled=True):
            if schedule.trigger_type != "event":
                continue
            if schedule.trigger_spec().get("event") != event_name:
                continue
            template = {**schedule.task_template(), "event": payload}
            _, run = self.runtime.create_task_run(
                goal=str(template.get("goal", schedule.name)),
                trigger_type="event_driven",
                trigger_event_id=schedule.schedule_id,
                template=template,
                requested_by=schedule.created_by,
                conversation_type=schedule.target_conversation_type,
                conversation_id=schedule.target_conversation_id,
            )
            self.runtime.submit(run.run_id)
            created.append(run.run_id)
        return created

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        self._running = True
        await self.tick(recovering=True)
        self._loop_task = asyncio.create_task(self._loop(), name="agent-scheduler")

    async def stop(self) -> None:
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.opt(exception=True).error("Scheduler tick failed")
            await asyncio.sleep(self.poll_interval)

    def _active_count(self, schedule_id: str) -> int:
        row = self.store.connection.execute(
            """SELECT COUNT(*) FROM task_runs r JOIN tasks t ON t.task_id=r.task_id
               WHERE t.trigger_event_id=? AND r.status NOT IN ('SUCCEEDED','FAILED','CANCELLED')""",
            (schedule_id,),
        ).fetchone()
        return int(row[0])


def _due_occurrences(
    schedule: ScheduleRecord, now: int, *, recovering: bool, limit: int
) -> tuple[list[int], int | None, bool]:
    due = schedule.next_run_at
    if due is None:
        return [], None, schedule.enabled
    spec = schedule.trigger_spec()
    occurrences: list[int] = []
    cursor = due
    while cursor <= now and len(occurrences) < limit:
        occurrences.append(cursor)
        cursor = _next_after(schedule.trigger_type, spec, schedule.timezone, cursor)
        if cursor is None:
            break

    if recovering and schedule.misfire_policy in {"skip", "run_once"}:
        latest, cursor = _fast_forward(
            schedule.trigger_type, spec, schedule.timezone, due, now
        )
        occurrences = [latest] if schedule.misfire_policy == "run_once" and latest else []

    enabled = not (schedule.trigger_type == "once" and cursor is None)
    return occurrences, cursor, enabled


def _fast_forward(
    trigger_type: str,
    spec: dict[str, Any],
    timezone: str,
    due: int,
    now: int,
) -> tuple[int | None, int | None]:
    if trigger_type == "once":
        return due, None
    if trigger_type == "interval":
        seconds = int(spec["seconds"])
        latest = due + ((now - due) // seconds) * seconds
        return latest, latest + seconds
    latest: int | None = None
    cursor: int | None = due
    while cursor is not None and cursor <= now:
        latest = cursor
        cursor = _next_after(trigger_type, spec, timezone, cursor)
    return latest, cursor


def _initial_next_run(
    trigger_type: str, spec: dict[str, Any], timezone: str, now: int
) -> int | None:
    if trigger_type == "once":
        return int(spec["at"])
    if trigger_type == "interval":
        return int(spec.get("start_at", now + int(spec["seconds"])))
    if trigger_type == "cron":
        return _next_cron(str(spec["expression"]), timezone, now)
    return None


def _next_after(
    trigger_type: str, spec: dict[str, Any], timezone: str, current: int
) -> int | None:
    if trigger_type == "once":
        return None
    if trigger_type == "interval":
        return current + int(spec["seconds"])
    if trigger_type == "cron":
        return _next_cron(str(spec["expression"]), timezone, current)
    return None


def _validate_schedule(
    trigger_type: str,
    spec: dict[str, Any],
    timezone: str,
    misfire_policy: str,
) -> None:
    if trigger_type not in {"once", "interval", "cron", "event"}:
        raise ValueError(f"unsupported trigger_type: {trigger_type}")
    if misfire_policy not in {"skip", "run_once", "catch_up"}:
        raise ValueError(f"unsupported misfire_policy: {misfire_policy}")
    try:
        _get_timezone(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone}") from exc
    if trigger_type == "once" and int(spec.get("at", 0)) <= 0:
        raise ValueError("once trigger requires positive 'at'")
    if trigger_type == "interval" and int(spec.get("seconds", 0)) <= 0:
        raise ValueError("interval trigger requires positive 'seconds'")
    if trigger_type == "cron":
        _parse_cron(str(spec.get("expression", "")))
    if trigger_type == "event" and not str(spec.get("event", "")):
        raise ValueError("event trigger requires 'event'")


def _next_cron(expression: str, timezone: str, after: int) -> int:
    fields = _parse_cron(expression)
    zone = _get_timezone(timezone)
    candidate = datetime.fromtimestamp(after, zone).replace(second=0, microsecond=0)
    candidate += timedelta(minutes=1)
    for _ in range(527_040):
        if (
            candidate.minute in fields[0]
            and candidate.hour in fields[1]
            and candidate.day in fields[2]
            and candidate.month in fields[3]
            and (candidate.weekday() + 1) % 7 in fields[4]
        ):
            return int(candidate.timestamp())
        candidate += timedelta(minutes=1)
    raise ValueError("cron expression has no occurrence within one year")


def _parse_cron(expression: str) -> list[set[int]]:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("cron expression must contain five fields")
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    return [
        _parse_cron_field(value, low, high)
        for value, (low, high) in zip(parts, ranges, strict=True)
    ]


def _parse_cron_field(value: str, low: int, high: int) -> set[int]:
    result: set[int] = set()
    for part in value.split(","):
        step = 1
        base = part
        if "/" in part:
            base, raw_step = part.split("/", 1)
            step = int(raw_step)
            if step <= 0:
                raise ValueError("cron step must be positive")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            raw_start, raw_end = base.split("-", 1)
            start, end = int(raw_start), int(raw_end)
        else:
            start = end = int(base)
        if start < low or end > high or start > end:
            raise ValueError(f"cron field out of range: {part}")
        result.update(range(start, end + 1, step))
    return result


def _get_timezone(name: str) -> tzinfo:
    # Windows Python distributions may not bundle the IANA database. Keep the
    # project's required default deterministic without making startup depend on
    # an optional tzdata wheel; other zones still use the platform database.
    if name in {"UTC", "Etc/UTC"}:
        return UTC
    if name == "Asia/Shanghai":
        return timezone(timedelta(hours=8), name="Asia/Shanghai")
    return ZoneInfo(name)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _schedule_from_row(row: tuple[Any, ...]) -> ScheduleRecord:
    values = list(row)
    values[2] = bool(values[2])
    return ScheduleRecord(*values)
