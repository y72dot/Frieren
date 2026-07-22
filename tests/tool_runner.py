"""Test adapter that invokes ToolExecutor directly.

Legacy test payloads are accepted temporarily so the tool behavior suite can
move off MessageBus without rewriting every fixture in the same phase.
"""

from __future__ import annotations

import json
from typing import Any

from src.core.llm.tool_permissions import ToolCallContext


async def execute_tool_calls(payload: dict[str, Any], bot) -> bool:
    """Execute test tool-call payloads through the production ToolExecutor."""
    bot.ensure_tool_platform()
    group_id: int | None = payload.get("group_id")
    user_id = int(payload.get("user_id") or 0)
    context = ToolCallContext(
        user_id=user_id,
        group_id=group_id,
        user_is_admin=user_id in bot.config.bot.admin_users,
        task_id=str(payload.get("task_id", "")) or None,
        run_id=str(payload.get("run_id", ""))
        or str(payload.get("session_key", ""))
        or None,
        step_id=str(payload.get("step_id", "")) or None,
        trace_id=str(payload.get("trace_id", "")),
        config_snapshot_id=str(payload.get("config_snapshot_id", "")),
    )

    results: list[dict[str, Any]] = []
    for tool_call in payload["tool_calls"]:
        if hasattr(tool_call, "name"):
            call_id = tool_call.id
            name = tool_call.name
            arguments = tool_call.arguments
        else:
            function = tool_call.get("function", {})
            call_id = tool_call.get("id", "")
            name = function.get("name", "")
            raw_arguments = function.get("arguments", "{}")
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else raw_arguments
            )
        result = await bot.tool_executor.execute(name, arguments, context, bot)
        results.append({"call_id": call_id, "name": name, "result": result})

    payload["response_buffer"]["results"] = results
    return False
