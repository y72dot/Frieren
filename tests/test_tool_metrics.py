from __future__ import annotations

import pytest

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef
from src.core.llm.tool_executor import ToolExecutor
from src.core.llm.tool_metrics import ToolMetrics
from src.core.llm.tool_permissions import ToolCallContext


def test_tool_metrics_snapshot_rates():
    metrics = ToolMetrics()
    metrics.record_view(registered=50, visible=7, schema_bytes=3465)
    metrics.record_tool_calls(["query_history", "missing"], {"query_history"})
    metrics.record_execution()
    metrics.record_execution()
    metrics.record_denied()
    metrics.record_unknown()

    snapshot = metrics.snapshot()
    assert snapshot.registered == 50
    assert snapshot.average_visible == 7
    assert snapshot.average_schema_bytes == 3465
    assert snapshot.selection_hit_rate == 0.5
    assert snapshot.first_selection_hit_rate == 1
    assert snapshot.average_calls_per_view == 2
    assert snapshot.denied_rate == 0.5
    assert snapshot.unknown_rate == 0.5


@pytest.mark.asyncio
async def test_executor_records_unknown_and_denied():
    async def execute(args, group_id, user_id, bot):
        return {"ok": True}

    catalog = ToolCatalog()
    catalog.register(
        ToolDef(
            name="admin_only",
            description="admin",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.READ_ONLY,
            category="test",
            executor=execute,
            requires_admin=True,
        )
    )
    executor = ToolExecutor(catalog)
    context = ToolCallContext(user_id=1, group_id=2, user_is_admin=False)

    assert "error" in await executor.execute("missing", {}, context, object())
    assert "error" in await executor.execute("admin_only", {}, context, object())

    snapshot = executor.metrics.snapshot()
    assert snapshot.executions == 2
    assert snapshot.unknown == 1
    assert snapshot.denied == 1
