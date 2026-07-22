from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from src.core.llm.invocation_store import InvocationStore
from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef
from src.core.llm.tool_executor import ToolExecutor
from src.core.llm.tool_permissions import ToolCallContext


def _context(**overrides) -> ToolCallContext:
    values = {
        "user_id": 1001,
        "group_id": 2001,
        "user_is_admin": False,
        "run_id": "run-1",
        "trace_id": "trace-1",
        "config_snapshot_id": "snapshot-1",
    }
    values.update(overrides)
    return ToolCallContext(**values)


def _tool(executor, **overrides) -> ToolDef:
    values = {
        "name": "demo",
        "description": "test tool",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["safe"]},
                "count": {"type": "integer", "minimum": 1},
                "api_key": {"type": "string"},
            },
            "required": ["mode", "count"],
            "additionalProperties": False,
        },
        "risk_level": RiskLevel.READ_ONLY,
        "category": "query",
        "executor": executor,
    }
    values.update(overrides)
    return ToolDef(**values)


def _platform(tool: ToolDef, **executor_options):
    connection = sqlite3.connect(":memory:")
    store = InvocationStore(connection)
    catalog = ToolCatalog()
    catalog.register(tool)
    return ToolExecutor(catalog, invocation_store=store, **executor_options), store


@pytest.mark.asyncio
async def test_current_tool_view_is_a_hard_execution_allowlist():
    calls = 0

    async def run(args, group_id, user_id, bot):
        nonlocal calls
        calls += 1
        return {"ok": True}

    executor, store = _platform(_tool(run, idempotency="keyed"))
    args = {"mode": "safe", "count": 1}

    allowed = await executor.execute(
        "demo",
        args,
        _context(run_id="run-allowed"),
        object(),
        allowed_tool_names={"demo"},
    )
    denied = await executor.execute(
        "demo",
        args,
        _context(run_id="run-denied"),
        object(),
        allowed_tool_names=set(),
    )

    assert allowed == {"ok": True}
    assert denied == {"error": "tool demo is not available in the current ToolView"}
    assert calls == 1
    assert store.list_for_run("run-denied")[0].status == "denied"
    assert executor.metrics.snapshot().denied == 1


@pytest.mark.asyncio
async def test_success_is_persisted_with_redacted_arguments():
    async def run(args, group_id, user_id, bot):
        return {"ok": True, "count": args["count"]}

    executor, store = _platform(_tool(run, version="2.1.0"))
    result = await executor.execute(
        "demo", {"mode": "safe", "count": 2, "api_key": "secret"}, _context(), object()
    )

    assert result == {"ok": True, "count": 2}
    invocation = store.list_for_run("run-1")[0]
    assert invocation.status == "succeeded"
    assert invocation.tool_version == "2.1.0"
    assert json.loads(invocation.arguments_json)["api_key"] == "***"
    assert invocation.trace_id == "trace-1"
    assert invocation.config_snapshot_id == "snapshot-1"


@pytest.mark.asyncio
async def test_full_schema_validation_rejects_before_execution():
    called = False

    async def run(args, group_id, user_id, bot):
        nonlocal called
        called = True
        return {"ok": True}

    executor, store = _platform(_tool(run))
    result = await executor.execute(
        "demo", {"mode": "unsafe", "count": 0, "extra": True}, _context(), object()
    )

    assert "error" in result
    assert called is False
    assert store.list_for_run("run-1")[0].status == "invalid"


@pytest.mark.asyncio
async def test_output_schema_and_size_limits_are_enforced():
    async def run(args, group_id, user_id, bot):
        return {"value": "too long"}

    tool = _tool(
        run,
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    )
    executor, store = _platform(tool, max_result_bytes=8)
    result = await executor.execute("demo", {"mode": "safe", "count": 1}, _context(), object())

    assert "error" in result
    assert store.list_for_run("run-1")[0].status == "failed"

    size_executor, size_store = _platform(
        _tool(run, output_schema={}), max_result_bytes=8
    )
    oversized = await size_executor.execute(
        "demo", {"mode": "safe", "count": 1}, _context(run_id="run-size"), object()
    )
    assert "exceeds" in oversized["error"]
    assert size_store.list_for_run("run-size")[0].status == "failed"


@pytest.mark.asyncio
async def test_permission_denial_and_timeout_are_persisted():
    async def run(args, group_id, user_id, bot):
        await asyncio.sleep(0.05)
        return {"ok": True}

    denied_executor, denied_store = _platform(
        _tool(run, scopes={"filesystem.write"})
    )
    denied = await denied_executor.execute(
        "demo", {"mode": "safe", "count": 1}, _context(), object()
    )
    assert "missing capabilities" in denied["error"]
    assert denied_store.list_for_run("run-1")[0].status == "denied"

    approval_executor, approval_store = _platform(
        _tool(run, approval="required")
    )
    approval_denied = await approval_executor.execute(
        "demo",
        {"mode": "safe", "count": 1},
        _context(user_is_admin=True, run_id="run-approval"),
        object(),
    )
    assert "requires approval" in approval_denied["error"]
    assert approval_store.list_for_run("run-approval")[0].status == "denied"

    timeout_executor, timeout_store = _platform(_tool(run, timeout_seconds=0.001))
    timed_out = await timeout_executor.execute(
        "demo", {"mode": "safe", "count": 1}, _context(), object()
    )
    assert "error" in timed_out
    assert timeout_store.list_for_run("run-1")[0].status == "timed_out"


@pytest.mark.asyncio
async def test_keyed_idempotency_executes_once_and_failed_attempt_can_retry():
    calls = 0

    async def run(args, group_id, user_id, bot):
        nonlocal calls
        calls += 1
        return {"call": calls}

    executor, store = _platform(_tool(run, idempotency="keyed"))
    args = {"mode": "safe", "count": 1}
    first = await executor.execute("demo", args, _context(), object())
    second = await executor.execute("demo", args, _context(), object())

    assert first == second == {"call": 1}
    assert calls == 1
    assert len(store.list_for_run("run-1")) == 1

    invalid = await executor.execute(
        "demo", {"mode": "safe", "count": 0}, _context(run_id="run-2"), object()
    )
    retried = await executor.execute(
        "demo", {"mode": "safe", "count": 0}, _context(run_id="run-2"), object()
    )
    assert "error" in invalid and "error" in retried
    assert len(store.list_for_run("run-2")) == 2


@pytest.mark.asyncio
async def test_unknown_tool_attempt_is_recorded():
    async def run(args, group_id, user_id, bot):
        return {}

    executor, store = _platform(_tool(run))
    result = await executor.execute("missing", {}, _context(), object())

    assert result == {"error": "unknown tool: missing"}
    invocation = store.list_for_run("run-1")[0]
    assert invocation.tool_name == "missing"
    assert invocation.tool_version == "unknown"
    assert invocation.status == "invalid"


def test_bot_tool_platform_is_instance_scoped_and_rebinds(bot_config):
    from src.core.bot import Bot
    from src.core.message_store import MessageStore

    first = Bot(config=bot_config)
    second = Bot(config=bot_config)
    assert first.tool_catalog is not second.tool_catalog
    assert first.tool_executor is not second.tool_executor

    async def run(args, group_id, user_id, bot):
        return {}

    first.tool_catalog.register(_tool(run, name="first_only"))
    assert first.tool_catalog.get("first_only") is not None
    assert second.tool_catalog.get("first_only") is None

    replacement = MessageStore(db_path=":memory:")
    first.msg_store = replacement
    first.ensure_tool_platform()
    assert first.invocation_store.connection is replacement.connection
    assert first.tool_catalog.get("first_only") is None
    assert first.tool_catalog.get("query_history") is not None


def test_catalog_assigns_effect_and_idempotency_defaults():
    async def run(args, group_id, user_id, bot):
        return {}

    catalog = ToolCatalog()
    tool = _tool(run, risk_level=RiskLevel.WRITE)
    catalog.register(tool)

    assert tool.effects == {"write"}
    assert tool.idempotency == "keyed"
