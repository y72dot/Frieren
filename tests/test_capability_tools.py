from __future__ import annotations

from src.core.bot import Bot
from src.core.llm.tool_permissions import ToolCallContext
from src.core.message_store import MessageStore


def _context() -> ToolCallContext:
    return ToolCallContext(
        user_id=111,
        group_id=456,
        user_is_admin=True,
        task_id="task-capability",
        run_id="run-capability",
    )


async def test_workspace_and_search_tools_share_artifact_and_invocation_chain(
    bot_config, tmp_path
):
    bot_config.workspace.root_dir = str(tmp_path / "workspace")
    bot_config.artifacts.root_dir = str(tmp_path / "artifacts")
    bot = Bot(config=bot_config)
    bot.msg_store = MessageStore(db_path=":memory:")
    bot.ensure_capability_services()
    bot.ensure_tool_platform()
    result = await bot.tool_executor.execute(
        "workspace_write",
        {
            "path": "reports/result.txt",
            "content": "traceable capability result",
            "export_artifact": True,
        },
        _context(),
        bot,
    )
    assert result["artifact"]["status"] == "available"

    searched = await bot.tool_executor.execute(
        "search_workspace", {"query": "capability"}, _context(), bot
    )
    assert searched["hits"][0]["reference"] == "workspace:reports/result.txt"
    invocations = bot.invocation_store.list_for_run("run-capability")
    assert [item.tool_name for item in invocations] == [
        "workspace_write",
        "search_workspace",
    ]
    assert all(item.status == "succeeded" for item in invocations)


async def test_control_tool_creates_pending_proposal_without_applying(bot_config):
    bot = Bot(config=bot_config)
    bot.msg_store = MessageStore(db_path=":memory:")
    bot.ensure_tool_platform()
    bot.ensure_control_plane()
    before = bot.config.llm.temperature
    result = await bot.tool_executor.execute(
        "settings_propose",
        {"changes": {"llm.temperature": 0.15}, "reason": "agent proposal"},
        _context(),
        bot,
    )
    assert result["status"] == "pending"
    assert result["risk"] == "medium"
    assert bot.config.llm.temperature == before
    assert bot.control_plane.get(result["proposal_id"]).status == "pending"
