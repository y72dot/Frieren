from __future__ import annotations

import asyncio
import time

import pytest

from src.core.llm import LlmResponse, ToolCall
from tests.conftest_e2e import (
    FakeLlmProvider,
    assert_api_called,
    dispatch_raw_event,
    e2e_bot,  # noqa: F401
    e2e_llm_bot,  # noqa: F401
)


def _message(text: str, message_id: int = 1) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "user_id": 111,
        "group_id": 456,
        "raw_message": f"[CQ:at,qq=123456] {text}",
        "message_id": message_id,
        "time": int(time.time()),
        "sender": {"nickname": "Alice"},
    }


async def _drain_runtime(bot) -> None:
    while bot.runtime._background:
        await asyncio.gather(*tuple(bot.runtime._background), return_exceptions=True)


@pytest.mark.asyncio
async def test_qq_agent_run_persists_task_steps_and_linked_invocation(e2e_llm_bot):  # noqa: F811
    provider = FakeLlmProvider()
    provider.responses = [
        LlmResponse(tool_calls=[ToolCall(id="clock", name="get_current_time", arguments={})]),
        LlmResponse(text="完成。"),
    ]
    e2e_llm_bot.llm_provider = provider

    await dispatch_raw_event(e2e_llm_bot, _message("现在几点？"))

    row = e2e_llm_bot.msg_store.connection.execute(
        "SELECT task_id, status FROM tasks ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row[1] == "SUCCEEDED"
    run = e2e_llm_bot.msg_store.connection.execute(
        "SELECT run_id, status FROM task_runs WHERE task_id=?", (row[0],)
    ).fetchone()
    assert run[1] == "SUCCEEDED"
    steps = e2e_llm_bot.runtime_store.list_steps(run[0])
    assert [step.kind for step in steps] == ["agent_loop", "tool"]
    assert all(step.status == "SUCCEEDED" for step in steps)
    invocation = e2e_llm_bot.invocation_store.list_for_run(run[0])[0]
    assert invocation.task_id == row[0]
    assert invocation.step_id == steps[1].step_id
    assert invocation.status == "succeeded"
    assert_api_called(e2e_llm_bot, "send_group_msg", message="完成。")


@pytest.mark.asyncio
async def test_due_schedule_creates_durable_run_and_uses_normal_reply_pipeline(e2e_llm_bot):  # noqa: F811
    provider = FakeLlmProvider()
    provider.responses = [LlmResponse(text="定时任务已执行。")]
    e2e_llm_bot.llm_provider = provider
    e2e_llm_bot.ensure_runtime_platform()
    schedule = e2e_llm_bot.schedule_store.create(
        name="one shot",
        trigger_type="once",
        trigger_spec={"at": 100},
        timezone="Asia/Shanghai",
        task_template={"kind": "agent_prompt", "goal": "提醒", "prompt": "请发送提醒"},
        target_conversation_type="group",
        target_conversation_id=456,
        created_by=111,
        now=0,
    )

    runs = await e2e_llm_bot.scheduler.tick(100)
    await _drain_runtime(e2e_llm_bot)

    assert len(runs) == 1
    assert e2e_llm_bot.runtime_store.get_run(runs[0]).status == "SUCCEEDED"
    task_id = e2e_llm_bot.runtime_store.get_run(runs[0]).task_id
    task = e2e_llm_bot.runtime_store.get_task(task_id)
    assert task.trigger_type == "scheduled"
    assert task.trigger_event_id == schedule.schedule_id
    assert e2e_llm_bot.schedule_store.get(schedule.schedule_id).enabled is False
    assert_api_called(e2e_llm_bot, "send_group_msg", message="定时任务已执行。")


@pytest.mark.asyncio
async def test_agent_creates_workspace_artifact_through_durable_tool_chain(
    e2e_llm_bot, tmp_path  # noqa: F811
):
    e2e_llm_bot.config.workspace.root_dir = str(tmp_path / "workspace")
    e2e_llm_bot.workspace = None
    e2e_llm_bot.ensure_capability_services()
    provider = FakeLlmProvider()
    provider.responses = [
        LlmResponse(
            tool_calls=[
                ToolCall(
                    id="write-file",
                    name="workspace_write",
                    arguments={
                        "path": "result.txt",
                        "content": "created by durable agent",
                        "export_artifact": True,
                    },
                )
            ]
        ),
        LlmResponse(text="文件已创建。"),
    ]
    e2e_llm_bot.llm_provider = provider

    await dispatch_raw_event(e2e_llm_bot, _message("创建结果文件", message_id=2))

    assert (tmp_path / "workspace" / "result.txt").read_text(encoding="utf-8") == (
        "created by durable agent"
    )
    row = e2e_llm_bot.msg_store.connection.execute(
        "SELECT source_type, status FROM artifacts WHERE file_name='result.txt'"
    ).fetchone()
    assert row == ("workspace", "available")
    run_id = e2e_llm_bot.msg_store.connection.execute(
        "SELECT run_id FROM task_runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]
    invocation = e2e_llm_bot.invocation_store.list_for_run(run_id)[0]
    assert invocation.tool_name == "workspace_write"
    assert invocation.status == "succeeded"
    assert_api_called(e2e_llm_bot, "send_group_msg", message="文件已创建。")
