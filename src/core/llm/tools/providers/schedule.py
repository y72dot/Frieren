"""LLM tool provider for durable schedules."""

from __future__ import annotations

from typing import Any

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef


async def _create_schedule(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    bot.ensure_runtime_platform()
    target_type = "group" if group_id is not None else "private"
    target_id = group_id if group_id is not None else user_id
    record = bot.scheduler.create(
        name=args["name"],
        trigger_type=args["trigger_type"],
        trigger_spec=args["trigger_spec"],
        timezone=args.get("timezone", bot.config.scheduler.timezone),
        task_template={
            "kind": "agent_prompt",
            "goal": args.get("goal", args["prompt"]),
            "prompt": args["prompt"],
        },
        target_conversation_type=target_type,
        target_conversation_id=target_id,
        created_by=user_id,
        misfire_policy=args.get("misfire_policy", "run_once"),
        max_concurrency=args.get("max_concurrency", 1),
    )
    return _schedule_dict(record)


async def _list_schedules(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    bot.ensure_runtime_platform()
    enabled = args.get("enabled")
    records = bot.schedule_store.list(enabled=enabled)
    return {"schedules": [_schedule_dict(record) for record in records]}


async def _set_schedule_enabled(
    args: dict, group_id: int | None, user_id: int | None, bot
) -> dict:
    bot.ensure_runtime_platform()
    bot.schedule_store.set_enabled(args["schedule_id"], args["enabled"])
    record = bot.schedule_store.get(args["schedule_id"])
    assert record is not None
    return _schedule_dict(record)


async def _delete_schedule(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    bot.ensure_runtime_platform()
    bot.schedule_store.delete(args["schedule_id"])
    return {"deleted": True, "schedule_id": args["schedule_id"]}


def _schedule_dict(record) -> dict[str, Any]:
    return {
        "schedule_id": record.schedule_id,
        "name": record.name,
        "enabled": record.enabled,
        "trigger_type": record.trigger_type,
        "trigger_spec": record.trigger_spec(),
        "timezone": record.timezone,
        "next_run_at": record.next_run_at,
        "last_run_at": record.last_run_at,
        "misfire_policy": record.misfire_policy,
        "target_conversation_type": record.target_conversation_type,
        "target_conversation_id": record.target_conversation_id,
    }


_TOOLS = [
    ToolDef(
        name="create_schedule",
        description="创建一次性、间隔、Cron 或事件驱动的持久化任务",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "goal": {"type": "string"},
                "prompt": {"type": "string", "minLength": 1},
                "trigger_type": {"type": "string", "enum": ["once", "interval", "cron", "event"]},
                "trigger_spec": {"type": "object"},
                "timezone": {"type": "string"},
                "misfire_policy": {"type": "string", "enum": ["skip", "run_once", "catch_up"]},
                "max_concurrency": {"type": "integer", "minimum": 1},
            },
            "required": ["name", "prompt", "trigger_type", "trigger_spec"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="scheduling",
        executor=_create_schedule,
        requires_admin=True,
        scopes={"schedule.write"},
    ),
    ToolDef(
        name="list_schedules",
        description="列出持久化定时任务及下次运行时间",
        parameters={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="scheduling",
        executor=_list_schedules,
    ),
    ToolDef(
        name="set_schedule_enabled",
        description="暂停或恢复定时任务",
        parameters={
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            "required": ["schedule_id", "enabled"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="scheduling",
        executor=_set_schedule_enabled,
        requires_admin=True,
        scopes={"schedule.write"},
    ),
    ToolDef(
        name="delete_schedule",
        description="永久删除定时任务",
        parameters={
            "type": "object",
            "properties": {"schedule_id": {"type": "string"}},
            "required": ["schedule_id"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.DESTRUCTIVE,
        category="scheduling",
        executor=_delete_schedule,
        requires_admin=True,
        scopes={"schedule.write"},
    ),
]


def register_schedule_tools(catalog: ToolCatalog) -> None:
    for tool in _TOOLS:
        catalog.register(tool)
