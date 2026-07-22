from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.core.runtime import (
    DurableRuntime,
    RuntimeStore,
    SchedulerService,
    ScheduleStore,
)


def _scheduler(*, max_catch_up: int = 10):
    connection = sqlite3.connect(":memory:")
    runtime = DurableRuntime(RuntimeStore(connection))
    store = ScheduleStore(connection)
    scheduler = SchedulerService(store, runtime, poll_interval=0.01, max_catch_up=max_catch_up)
    return scheduler, runtime, store


async def _drain(runtime: DurableRuntime) -> None:
    while runtime._background:
        await asyncio.gather(*tuple(runtime._background), return_exceptions=True)


@pytest.mark.asyncio
async def test_interval_tick_creates_task_run_instead_of_executing_in_scheduler():
    scheduler, runtime, store = _scheduler()
    calls = []

    async def handler(template, context):
        calls.append((template["value"], context.run_id))
        return {"ok": True}

    runtime.register_handler("test", handler)
    record = store.create(
        name="interval",
        trigger_type="interval",
        trigger_spec={"seconds": 60, "start_at": 100},
        timezone="Asia/Shanghai",
        task_template={"kind": "test", "value": 7},
        now=0,
    )
    runs = await scheduler.tick(100)
    await _drain(runtime)

    assert len(runs) == 1
    assert calls == [(7, runs[0])]
    assert runtime.store.get_run(runs[0]).status == "SUCCEEDED"
    assert store.get(record.schedule_id).next_run_at == 160


@pytest.mark.asyncio
@pytest.mark.parametrize("policy, expected", [("skip", 0), ("run_once", 1), ("catch_up", 4)])
async def test_recovery_misfire_policies(policy, expected):
    scheduler, runtime, store = _scheduler()

    async def handler(template, context):
        return {}

    runtime.register_handler("test", handler)
    schedule = store.create(
        name=policy,
        trigger_type="interval",
        trigger_spec={"seconds": 10, "start_at": 10},
        timezone="UTC",
        task_template={"kind": "test", "allow_catch_up": True},
        misfire_policy=policy,
        max_concurrency=10,
        now=0,
    )
    runs = await scheduler.tick(40, recovering=True)
    await _drain(runtime)
    assert len(runs) == expected
    if policy in {"skip", "run_once"}:
        assert store.get(schedule.schedule_id).next_run_at == 50
        assert await scheduler.tick(40) == []


def test_agent_prompt_catch_up_requires_explicit_override():
    _, _, store = _scheduler()
    with pytest.raises(ValueError, match="catch_up is disabled"):
        store.create(
            name="messages",
            trigger_type="interval",
            trigger_spec={"seconds": 10},
            timezone="UTC",
            task_template={"kind": "agent_prompt", "prompt": "hello"},
            misfire_policy="catch_up",
            now=0,
        )


@pytest.mark.asyncio
async def test_once_schedule_disables_after_run_and_event_schedule_dispatches():
    scheduler, runtime, store = _scheduler()
    seen = []

    async def handler(template, context):
        seen.append(template.get("event", {}).get("id", "once"))
        return {}

    runtime.register_handler("test", handler)
    once = store.create(
        name="once",
        trigger_type="once",
        trigger_spec={"at": 100},
        timezone="UTC",
        task_template={"kind": "test"},
        now=0,
    )
    await scheduler.tick(100)
    await _drain(runtime)
    assert store.get(once.schedule_id).enabled is False

    store.create(
        name="event",
        trigger_type="event",
        trigger_spec={"event": "artifact.available"},
        timezone="UTC",
        task_template={"kind": "test"},
        now=0,
    )
    runs = await scheduler.trigger_event("artifact.available", {"id": "a1"})
    await _drain(runtime)
    assert len(runs) == 1
    assert seen == ["once", "a1"]


def test_cron_uses_explicit_timezone_and_standard_sunday_numbering():
    _, _, store = _scheduler()
    zone = timezone(timedelta(hours=8), name="Asia/Shanghai")
    saturday = int(datetime(2026, 7, 25, 23, 59, tzinfo=zone).timestamp())
    record = store.create(
        name="sunday",
        trigger_type="cron",
        trigger_spec={"expression": "0 9 * * 0"},
        timezone="Asia/Shanghai",
        task_template={"kind": "test"},
        now=saturday,
    )
    expected = int(datetime(2026, 7, 26, 9, 0, tzinfo=zone).timestamp())
    assert record.next_run_at == expected


def test_schedule_validation_rejects_bad_inputs():
    _, _, store = _scheduler()
    with pytest.raises(ValueError, match="five fields"):
        store.create(
            name="bad",
            trigger_type="cron",
            trigger_spec={"expression": "* *"},
            timezone="UTC",
            task_template={"kind": "test"},
        )
    with pytest.raises(ValueError, match="unknown timezone"):
        store.create(
            name="bad-zone",
            trigger_type="interval",
            trigger_spec={"seconds": 10},
            timezone="Mars/Olympus",
            task_template={"kind": "test"},
        )
