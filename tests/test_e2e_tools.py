"""E2E tool execution tests through the production ToolExecutor."""

from __future__ import annotations

import time

import pytest

from src.core.llm import ToolCall
from tests.conftest_e2e import e2e_bot  # noqa: F401


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _run_tool(bot, tool_calls: list[ToolCall], group_id=456, user_id=111):
    """Execute tool calls directly and return normalized results."""
    from tests.tool_runner import execute_tool_calls as llm_tools_handler

    buf: dict = {}
    await llm_tools_handler(
        {
            "llm_type": "tool",
            "tool_calls": tool_calls,
            "response_buffer": buf,
            "group_id": group_id,
            "user_id": user_id,
        },
        bot,
    )
    return buf.get("results", [])


def _reset_calls(bot) -> None:
    bot.api.calls.clear()


def _assert_tool_result(results, call_id, expected_key=None):
    """Assert results contain an entry for call_id, optionally with expected_key."""
    for r in results:
        if r["call_id"] == call_id:
            if expected_key:
                assert expected_key in r["result"], f"Missing key {expected_key!r} in {r['result']}"
            return r["result"]
    pytest.fail(f"No result for call_id={call_id} in {results}")


# ---------------------------------------------------------------------------
# Category: Local tools (no API calls)
# ---------------------------------------------------------------------------


class TestLocalTools:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_current_time(self, e2e_bot):
        """get_current_time returns datetime without API calls."""
        before = len(e2e_bot.api.calls)
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="get_current_time", arguments={})]
        )
        after = len(e2e_bot.api.calls)

        result = _assert_tool_result(results, "c1", "datetime")
        assert "202" in result["datetime"]
        assert len(result["datetime"]) == 19
        assert after == before  # No API calls

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_think(self, e2e_bot):
        """think tool returns acknowledged True, no API calls."""
        before = len(e2e_bot.api.calls)
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="think",
                    arguments={"reasoning": "I need to analyze this."},
                )
            ],
        )
        after = len(e2e_bot.api.calls)

        result = _assert_tool_result(results, "c1", "acknowledged")
        assert result["acknowledged"] is True
        assert after == before

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_tool_help_all(self, e2e_bot):
        """tool_help without tool_name lists all tools."""
        before = len(e2e_bot.api.calls)
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="tool_help", arguments={})]
        )
        after = len(e2e_bot.api.calls)

        result = _assert_tool_result(results, "c1", "text")
        assert "可用工具" in result["text"]
        assert after == before

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_tool_help_single(self, e2e_bot):
        """tool_help with tool_name gives detailed usage."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="tool_help",
                    arguments={"tool_name": "mute_user"},
                )
            ],
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "mute_user" in result["text"]
        assert "禁言" in result["text"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_unknown_tool_error(self, e2e_bot):
        """Unknown tool name returns error message."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="nonexistent_tool", arguments={})],
        )
        result = _assert_tool_result(results, "c1", "error")
        assert "unknown tool" in result["error"]


# ---------------------------------------------------------------------------
# Category: Management tools
# ---------------------------------------------------------------------------


class TestManagementTools:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_set_essence(self, e2e_bot):
        """set_essence calls set_essence_msg API."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="set_essence", arguments={"message_id": 12345})],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "set_essence_msg" and c.get("message_id") == 12345
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_remove_essence(self, e2e_bot):
        """remove_essence calls delete_essence_msg API."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="remove_essence", arguments={"message_id": 9999})],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "delete_essence_msg" and c.get("message_id") == 9999
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_mute_user(self, e2e_bot):
        """mute_user calls set_group_ban with group_id, user_id, duration."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="mute_user",
                    arguments={"user_id": 999, "duration": 600},
                )
            ],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "set_group_ban"
            and c.get("group_id") == 456
            and c.get("user_id") == 999
            and c.get("duration") == 600
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_kick_user(self, e2e_bot):
        """kick_user calls set_group_kick."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="kick_user", arguments={"user_id": 777})],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "set_group_kick"
            and c.get("group_id") == 456
            and c.get("user_id") == 777
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_set_group_card(self, e2e_bot):
        """set_group_card calls set_group_card action."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="set_group_card",
                    arguments={"user_id": 333, "card": "NewName"},
                )
            ],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "set_group_card"
            and c.get("params", {}).get("user_id") == 333
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_delete_msg(self, e2e_bot):
        """delete_msg calls delete_msg action."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="delete_msg", arguments={"message_id": 5555})],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "delete_msg"
            and c.get("params", {}).get("message_id") == 5555
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_whole_ban_enable(self, e2e_bot):
        """whole_ban with enable=True calls set_group_whole_ban."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="whole_ban", arguments={"enable": True})],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "set_group_whole_ban"
            and c.get("params", {}).get("enable") is True
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_set_admin(self, e2e_bot):
        """set_admin calls set_group_admin action."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="set_admin",
                    arguments={"user_id": 444, "enable": True},
                )
            ],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "set_group_admin"
            and c.get("params", {}).get("user_id") == 444
            for c in e2e_bot.api.calls
        )


# ---------------------------------------------------------------------------
# Category: Interaction tools
# ---------------------------------------------------------------------------


class TestInteractionTools:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_react_emoji(self, e2e_bot):
        """react_emoji calls set_msg_emoji_like."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="react_emoji",
                    arguments={"message_id": 1111, "emoji_id": 128077},
                )
            ],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "set_msg_emoji_like"
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_send_message_group(self, e2e_bot):
        """send_message in group context calls send_group_msg."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="send_message",
                    arguments={"text": "Notice: meeting at 3pm"},
                )
            ],
            group_id=456,
        )
        _assert_tool_result(results, "c1", "sent")
        assert any(
            c.get("method") == "send_group_msg" and c.get("group_id") == 456
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_send_message_private(self, e2e_bot):
        """send_message in private context calls send_private_msg."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="send_message", arguments={"text": "hello"})],
            group_id=None,
            user_id=789,
        )
        _assert_tool_result(results, "c1", "sent")
        assert any(
            c.get("method") == "send_private_msg" and c.get("user_id") == 789
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_send_poke(self, e2e_bot):
        """send_poke calls send_group_poke."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="send_poke", arguments={"user_id": 222})],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "send_group_poke"
            and c.get("group_id") == 456
            and c.get("user_id") == 222
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_send_like(self, e2e_bot):
        """send_like calls send_like action."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="send_like",
                    arguments={"user_id": 333, "times": 3},
                )
            ],
            group_id=None,
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "send_like"
            for c in e2e_bot.api.calls
        )


# ---------------------------------------------------------------------------
# Category: Query tools
# ---------------------------------------------------------------------------


class TestQueryTools:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_group_info(self, e2e_bot):
        """get_group_info calls get_group_info API and formats result."""
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="get_group_info", arguments={})]
        )
        _assert_tool_result(results, "c1", "text")
        assert any(
            c.get("method") == "get_group_info" and c.get("group_id") == 456
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_member_info(self, e2e_bot):
        """get_member_info calls get_group_member_info."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="get_member_info", arguments={"user_id": 555})],
        )
        _assert_tool_result(results, "c1", "text")
        assert any(
            c.get("method") == "get_group_member_info"
            and c.get("user_id") == 555
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_member_list(self, e2e_bot):
        """get_member_list calls get_group_member_list and formats result."""
        e2e_bot.api.set_response(
            "get_group_member_list",
            {
                "data": [
                    {"user_id": 1, "nickname": "Alice", "card": "", "role": "owner"},
                    {"user_id": 2, "nickname": "Bob", "card": "Bobby", "role": "member"},
                ]
            },
        )
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="get_member_list", arguments={})]
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "Alice" in result["text"]
        assert "Bob" in result["text"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_essence_list(self, e2e_bot):
        """get_essence_list returns formatted essence list."""
        e2e_bot.api.set_response(
            "call_action",
            {
                "data": [
                    {
                        "message_id": 100,
                        "sender_nick": "Alice",
                        "content": "Good work!",
                        "time": 1700000000,
                    }
                ]
            },
        )
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="get_essence_list", arguments={})]
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "精华" in result["text"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_shut_list(self, e2e_bot):
        """get_shut_list returns formatted shut-up list."""
        e2e_bot.api.set_response(
            "call_action",
            {
                "data": [
                    {"user_id": 777, "nickname": "Trouble", "duration": 300}
                ]
            },
        )
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="get_shut_list", arguments={})]
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "禁言" in result["text"] or "Trouble" in result["text"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_query_history_default(self, e2e_bot):
        """query_history without args returns recent group messages."""
        # Seed msg_store
        e2e_bot.msg_store.record_bot_message(
            message_id=1, group_id=456, user_id=111,
            nickname="Alice", content="Hello everyone",
            time=int(time.time()), is_group=True,
        )
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="query_history", arguments={})]
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "Alice" in result["text"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_query_history_by_user(self, e2e_bot):
        """query_history(user_id) filters by user."""
        now = int(time.time())
        e2e_bot.msg_store.record_bot_message(
            message_id=10, group_id=456, user_id=111,
            nickname="Alice", content="msg1", time=now, is_group=True,
        )
        e2e_bot.msg_store.record_bot_message(
            message_id=20, group_id=456, user_id=222,
            nickname="Bob", content="msg2", time=now + 1, is_group=True,
        )
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="query_history", arguments={"user_id": 111})],
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "Alice" in result["text"]
        assert "Bob" not in result["text"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_query_history_by_keyword(self, e2e_bot):
        """query_history(keyword) searches message content."""
        now = int(time.time())
        e2e_bot.msg_store.record_bot_message(
            message_id=30, group_id=456, user_id=111,
            nickname="Alice", content="meeting at 3pm", time=now, is_group=True,
        )
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="query_history", arguments={"keyword": "meeting"})],
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "meeting" in result["text"]


# ---------------------------------------------------------------------------
# Category: Content perception tools
# ---------------------------------------------------------------------------


class TestPerceptionTools:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_ocr_image(self, e2e_bot):
        """ocr_image calls ocr_image API."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="ocr_image",
                    arguments={"image": "http://example.com/img.png"},
                )
            ],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action" and c.get("action") == "ocr_image"
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_voice_to_text(self, e2e_bot):
        """voice_to_text calls fetch_ptt_text API."""
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="voice_to_text",
                    arguments={"message_id": 8888},
                )
            ],
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "fetch_ptt_text"
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_resolve_forward(self, e2e_bot):
        """resolve_forward calls get_forward_msg and parses result."""
        e2e_bot.api.set_response(
            "get_forward_msg",
            {
                "data": {
                    "messages": [
                        {
                            "sender": {"nickname": "Alice", "user_id": 111},
                            "message": [{"type": "text", "data": {"text": "Hello"}}],
                        }
                    ]
                }
            },
        )
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="resolve_forward",
                    arguments={"forward_id": "fwd123"},
                )
            ],
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "Alice" in result["text"]
        assert "Hello" in result["text"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_resolve_forward_nested(self, e2e_bot):
        """resolve_forward handles nested forwards."""
        e2e_bot.api.set_response(
            "get_forward_msg",
            {
                "data": {
                    "messages": [
                        {
                            "sender": {"nickname": "Alice", "user_id": 111},
                            "message": [
                                {
                                    "type": "forward",
                                    "data": {"id": "nested_fwd"},
                                }
                            ],
                        }
                    ]
                }
            },
        )
        results = await _run_tool(
            e2e_bot,
            [
                ToolCall(
                    id="c1",
                    name="resolve_forward",
                    arguments={"forward_id": "outer"},
                )
            ],
        )
        result = _assert_tool_result(results, "c1", "text")
        assert "Alice" in result["text"]


# ---------------------------------------------------------------------------
# Category: Error & edge cases
# ---------------------------------------------------------------------------


class TestToolEdgeCases:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_mute_user_missing_user_id(self, e2e_bot):
        """mute_user without user_id raises KeyError → captured as error."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="mute_user", arguments={"duration": 60})],
        )
        result = _assert_tool_result(results, "c1", "error")
        assert "user_id" in result["error"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_whole_ban_default_enable(self, e2e_bot):
        """whole_ban without enable arg defaults to True."""
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="whole_ban", arguments={})]
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "set_group_whole_ban"
            and c.get("params", {}).get("enable") is True
            for c in e2e_bot.api.calls
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_send_like_private_no_group_id(self, e2e_bot):
        """send_like in private context works fine without group_id."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="send_like", arguments={"user_id": 789})],
            group_id=None,
            user_id=789,
        )
        _assert_tool_result(results, "c1")
        assert any(
            c.get("method") == "call_action" and c.get("action") == "send_like"
            for c in e2e_bot.api.calls
        )
