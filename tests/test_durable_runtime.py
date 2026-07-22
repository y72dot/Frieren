from __future__ import annotations

import sqlite3

import pytest

from src.core.llm.invocation_store import InvocationStore
from src.core.runtime import DurableRuntime, RecoveryController, RuntimeStore


def _runtime():
    connection = sqlite3.connect(":memory:")
    store = RuntimeStore(connection)
    return DurableRuntime(store), connection


def test_task_run_step_lifecycle_and_attempts_are_persistent():
    runtime, connection = _runtime()
    task, run = runtime.create_task_run(
        goal="archive a file",
        trigger_type="qq_message",
        template={"kind": "test", "value": 1},
        requested_by=1001,
        conversation_type="group",
        conversation_id=2001,
    )
    runtime.store.update_task(task.task_id, "RUNNING")
    runtime.store.update_run(run.run_id, "RUNNING")
    step = runtime.store.create_step(run.run_id, "tool", input_data={"x": 1})
    runtime.store.update_step(step.step_id, "SUCCEEDED", output={"ok": True})
    runtime.store.update_run(run.run_id, "SUCCEEDED")
    runtime.store.update_task(task.task_id, "SUCCEEDED")

    reopened = RuntimeStore(connection)
    assert reopened.get_task(task.task_id).status == "SUCCEEDED"
    assert reopened.get_run(run.run_id).status == "SUCCEEDED"
    assert reopened.get_step(step.step_id).output() == {"ok": True}
    retry = reopened.create_run(task.task_id)
    assert retry.attempt == 2


@pytest.mark.asyncio
async def test_registered_handler_completes_run_and_failure_is_durable():
    runtime, _ = _runtime()

    async def success(template, context):
        return {"echo": template["value"], "step_id": context.step_id}

    runtime.register_handler("test", success)
    task, run = runtime.create_task_run(
        goal="test", trigger_type="manual", template={"kind": "test", "value": 7}
    )
    result = await runtime.execute_run(run.run_id)
    assert result["echo"] == 7
    assert runtime.store.get_task(task.task_id).status == "SUCCEEDED"
    assert runtime.store.get_run(run.run_id).status == "SUCCEEDED"
    assert runtime.store.list_steps(run.run_id)[0].status == "SUCCEEDED"

    async def failure(template, context):
        raise RuntimeError("boom")

    runtime.register_handler("fail", failure)
    failed_task, failed_run = runtime.create_task_run(
        goal="fail", trigger_type="manual", template={"kind": "fail"}
    )
    with pytest.raises(RuntimeError, match="boom"):
        await runtime.execute_run(failed_run.run_id)
    assert runtime.store.get_task(failed_task.task_id).status == "FAILED"
    assert runtime.store.get_run(failed_run.run_id).error == "boom"


def test_wait_resume_and_cancel_require_matching_token():
    runtime, _ = _runtime()
    task, run = runtime.create_task_run(
        goal="wait", trigger_type="manual", template={"kind": "test"}
    )
    runtime.wait(run.run_id, "artifact", "artifact:123")
    assert runtime.store.get_run(run.run_id).status == "WAITING_ARTIFACT"
    assert runtime.store.get_task(task.task_id).status == "WAITING_ARTIFACT"
    with pytest.raises(PermissionError):
        runtime.resume(run.run_id, "wrong")
    runtime.resume(run.run_id, "artifact:123")
    assert runtime.store.get_run(run.run_id).status == "CREATED"
    runtime.cancel(run.run_id)
    assert runtime.store.get_task(task.task_id).status == "CANCELLED"


def test_recovery_uses_invocation_result_and_quarantines_unknown_outcome():
    runtime, connection = _runtime()
    invocations = InvocationStore(connection)
    task, run = runtime.create_task_run(
        goal="recover", trigger_type="manual", template={"kind": "test"}
    )
    runtime.store.update_task(task.task_id, "RUNNING")
    runtime.store.update_run(run.run_id, "RUNNING")
    step = runtime.store.create_step(run.run_id, "tool", status="RUNNING")
    invocation = invocations.begin(
        tool_name="demo",
        tool_version="1",
        arguments={},
        run_id=run.run_id,
        task_id=task.task_id,
        step_id=step.step_id,
        invocation_id=None,
        idempotency_key="safe",
        trace_id="trace",
        user_id=1,
        group_id=2,
        config_snapshot_id="snapshot",
    )
    invocations.transition(invocation.invocation_id, "succeeded", result={"ok": True}, terminal=True)

    recovered = RecoveryController(runtime, invocations).recover()
    assert recovered == [run.run_id]
    assert runtime.store.get_step(step.step_id).output() == {"ok": True}
    assert runtime.store.get_run(run.run_id).status == "CREATED"

    task2, run2 = runtime.create_task_run(
        goal="unknown", trigger_type="manual", template={"kind": "test"}
    )
    runtime.store.update_task(task2.task_id, "RUNNING")
    runtime.store.update_run(run2.run_id, "RUNNING")
    step2 = runtime.store.create_step(run2.run_id, "tool", status="RUNNING")
    pending = invocations.begin(
        tool_name="write",
        tool_version="1",
        arguments={},
        run_id=run2.run_id,
        task_id=task2.task_id,
        step_id=step2.step_id,
        invocation_id=None,
        idempotency_key="write-key",
        trace_id="",
        user_id=1,
        group_id=2,
        config_snapshot_id="",
    )
    invocations.transition(pending.invocation_id, "running")
    RecoveryController(runtime, invocations).recover()
    assert runtime.store.get_run(run2.run_id).status == "WAITING_APPROVAL"


def test_invocation_schema_migrates_existing_database_with_step_id():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """CREATE TABLE tool_invocations (
            invocation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT,
            tool_name TEXT NOT NULL, tool_version TEXT NOT NULL,
            arguments_json TEXT NOT NULL, status TEXT NOT NULL,
            idempotency_key TEXT, result_json TEXT, error TEXT,
            started_at INTEGER, ended_at INTEGER, trace_id TEXT NOT NULL DEFAULT '',
            user_id INTEGER, group_id INTEGER, config_snapshot_id TEXT NOT NULL DEFAULT ''
        )"""
    )
    InvocationStore(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(tool_invocations)")}
    assert "step_id" in columns
