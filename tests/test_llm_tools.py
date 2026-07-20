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

        assert len(TOOL_DEFS) == 7

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


class TestQueryHistory:
    @pytest.mark.asyncio
    async def test_query_recent_group_messages(self, bot):
        """query_history without args returns recent group messages."""
        from plugins.llm_tools import llm_tools_handler

        # Insert test messages
        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "hello", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "world", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh1", "function": {"name": "query_history", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        assert len(response_buf["results"]) == 1
        text = response_buf["results"][0]["result"]["text"]
        assert "找到以下消息" in text
        assert "[1] Alice(100): hello" in text
        assert "[2] Bob(200): world" in text

    @pytest.mark.asyncio
    async def test_query_with_keyword(self, bot):
        """query_history with keyword filters by search."""
        from plugins.llm_tools import llm_tools_handler

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "hello", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "goodbye", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh2", "function": {"name": "query_history", "arguments": '{"keyword": "hello"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "hello" in text
        assert "goodbye" not in text

    @pytest.mark.asyncio
    async def test_query_by_user(self, bot):
        """query_history with user_id filters by user."""
        from plugins.llm_tools import llm_tools_handler

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "msg1", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "msg2", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh3", "function": {"name": "query_history", "arguments": '{"user_id": 100}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "Alice" in text
        assert "Bob" not in text

    @pytest.mark.asyncio
    async def test_query_private_messages(self, bot):
        """query_history in private chat returns private messages."""
        from plugins.llm_tools import llm_tools_handler

        bot.msg_store.record_bot_message(1, None, 999, "User", "private msg", 1000, False)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh4", "function": {"name": "query_history", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": None,
                "user_id": 999,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "private msg" in text

    @pytest.mark.asyncio
    async def test_query_no_results(self, bot):
        """query_history with no matching messages returns提示."""
        from plugins.llm_tools import llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh5", "function": {"name": "query_history", "arguments": '{"keyword": "nonexistent"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "没有找到" in text

    @pytest.mark.asyncio
    async def test_query_respects_limit(self, bot):
        """query_history limit clamps at 30."""
        from plugins.llm_tools import llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh6", "function": {"name": "query_history", "arguments": '{"limit": 100}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        # Should not error, limit clamped to 30
        assert len(response_buf["results"]) == 1
        assert "result" in response_buf["results"][0]

    @pytest.mark.asyncio
    async def test_query_excludes_bot_messages(self, bot):
        """query_history recent excludes bot's own messages."""
        from plugins.llm_tools import llm_tools_handler

        bot_qq = bot.config.bot.qq
        bot.msg_store.record_bot_message(1, 123, bot_qq, "Bot", "bot msg", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 100, "Alice", "user msg", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh7", "function": {"name": "query_history", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "user msg" in text
        assert "bot msg" not in text
