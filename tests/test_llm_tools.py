"""Tests for llm_tools – tool definition validation and execution."""

from __future__ import annotations

import pytest

from src.core.llm import ToolCall


class TestToolDefs:
    def test_all_tools_have_required_fields(self):
        from plugins.llm_tools import TOOL_DEFS

        for tool_def in TOOL_DEFS:
            assert tool_def["type"] == "function"
            fn = tool_def["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_tool_count(self):
        from plugins.llm_tools import TOOL_DEFS

        assert len(TOOL_DEFS) == 6

    def test_all_tool_names_unique(self):
        from plugins.llm_tools import TOOL_DEFS

        names = [t["function"]["name"] for t in TOOL_DEFS]
        assert len(names) == len(set(names))


class TestLlmToolsHandler:
    @pytest.mark.asyncio
    async def test_no_match(self, bot):
        """Returns False for non-tool llm_type payloads."""
        from plugins.llm_tools import llm_tools_handler

        result = await llm_tools_handler({"llm_type": "other"}, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_set_essence(self, bot):
        """set_essence tool calls bot.api.set_essence_msg."""
        from plugins.llm_tools import llm_tools_handler

        tc = ToolCall(id="call_1", name="set_essence", arguments={"message_id": 42})
        response_buf: dict = {}

        result = await llm_tools_handler(
            {
                "llm_type": "tool",
                "session_key": "group:123",
                "tool_calls": [tc],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        assert result is False
        assert "results" in response_buf
        assert len(response_buf["results"]) == 1
        assert response_buf["results"][0]["name"] == "set_essence"
        assert response_buf["results"][0]["call_id"] == "call_1"

        # Verify API was called
        calls = [c for c in bot.api.calls if c.get("method") == "set_essence_msg"]
        assert len(calls) == 1
        assert calls[0]["message_id"] == 42

    @pytest.mark.asyncio
    async def test_execute_send_message_group(self, bot):
        """send_message tool sends to group."""
        from plugins.llm_tools import llm_tools_handler

        tc = ToolCall(
            id="call_2", name="send_message", arguments={"text": "hello group"}
        )
        response_buf: dict = {}

        await llm_tools_handler(
            {
                "llm_type": "tool",
                "session_key": "group:123",
                "tool_calls": [tc],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        calls = [c for c in bot.api.calls if c.get("method") == "send_group_msg"]
        assert len(calls) == 1
        assert calls[0]["group_id"] == 123
        assert calls[0]["message"] == "hello group"

    @pytest.mark.asyncio
    async def test_execute_send_message_private(self, bot):
        """send_message tool sends to private chat when no group_id."""
        from plugins.llm_tools import llm_tools_handler

        tc = ToolCall(
            id="call_3", name="send_message", arguments={"text": "hello private"}
        )
        response_buf: dict = {}

        await llm_tools_handler(
            {
                "llm_type": "tool",
                "session_key": "private:999",
                "tool_calls": [tc],
                "response_buffer": response_buf,
                "group_id": None,
                "user_id": 999,
            },
            bot,
        )

        calls = [c for c in bot.api.calls if c.get("method") == "send_private_msg"]
        assert len(calls) == 1
        assert calls[0]["user_id"] == 999

    @pytest.mark.asyncio
    async def test_execute_mute_user(self, bot):
        """mute_user tool calls bot.api.set_group_ban."""
        from plugins.llm_tools import llm_tools_handler

        tc = ToolCall(
            id="call_4", name="mute_user", arguments={"user_id": 555, "duration": 600}
        )
        response_buf: dict = {}

        await llm_tools_handler(
            {
                "llm_type": "tool",
                "session_key": "group:123",
                "tool_calls": [tc],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        calls = [c for c in bot.api.calls if c.get("method") == "set_group_ban"]
        assert len(calls) == 1
        assert calls[0]["user_id"] == 555
        assert calls[0]["duration"] == 600

    @pytest.mark.asyncio
    async def test_execute_kick_user(self, bot):
        """kick_user tool calls bot.api.set_group_kick."""
        from plugins.llm_tools import llm_tools_handler

        tc = ToolCall(
            id="call_5", name="kick_user", arguments={"user_id": 777}
        )
        response_buf: dict = {}

        await llm_tools_handler(
            {
                "llm_type": "tool",
                "session_key": "group:123",
                "tool_calls": [tc],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        calls = [c for c in bot.api.calls if c.get("method") == "set_group_kick"]
        assert len(calls) == 1
        assert calls[0]["user_id"] == 777

    @pytest.mark.asyncio
    async def test_execute_multiple_tools(self, bot):
        """Multiple tool calls in one dispatch are all executed."""
        from plugins.llm_tools import llm_tools_handler

        tc1 = ToolCall(
            id="call_a", name="set_essence", arguments={"message_id": 1}
        )
        tc2 = ToolCall(
            id="call_b", name="set_essence", arguments={"message_id": 2}
        )
        response_buf: dict = {}

        await llm_tools_handler(
            {
                "llm_type": "tool",
                "session_key": "group:123",
                "tool_calls": [tc1, tc2],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        assert len(response_buf["results"]) == 2
        essence_calls = [
            c for c in bot.api.calls if c.get("method") == "set_essence_msg"
        ]
        assert len(essence_calls) == 2

    @pytest.mark.asyncio
    async def test_unknown_tool(self, bot):
        """Unknown tool returns error."""
        from plugins.llm_tools import llm_tools_handler

        tc = ToolCall(
            id="call_err", name="nonexistent_tool", arguments={}
        )
        response_buf: dict = {}

        await llm_tools_handler(
            {
                "llm_type": "tool",
                "session_key": "group:123",
                "tool_calls": [tc],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        assert response_buf["results"][0]["result"] == {
            "error": "unknown tool: nonexistent_tool"
        }

    @pytest.mark.asyncio
    async def test_tool_execution_error(self, bot):
        """Tool execution exceptions are caught and returned as errors."""
        from plugins.llm_tools import llm_tools_handler

        tc = ToolCall(
            id="call_err",
            name="set_essence",
            arguments={"message_id": 99},
        )
        response_buf: dict = {}

        # Make set_essence_msg fail
        async def fail_essence(*a, **kw):
            raise RuntimeError("API down")

        bot.api.set_essence_msg = fail_essence

        await llm_tools_handler(
            {
                "llm_type": "tool",
                "session_key": "group:123",
                "tool_calls": [tc],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        assert "error" in response_buf["results"][0]["result"]
        assert "API down" in response_buf["results"][0]["result"]["error"]
