"""Tests for llm_tools – tool definition validation and execution."""

from __future__ import annotations

from datetime import UTC

import pytest

from src.core.llm import ToolCall


class TestToolDefs:
    def test_all_tools_have_required_fields(self):
        from src.core.llm.tools.providers.qq import TOOL_DEFS

        for tool_def in TOOL_DEFS:
            assert tool_def["type"] == "function"
            fn = tool_def["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_qq_provider_contract(self):
        from src.core.llm.tools.providers.qq import TOOL_DEFS

        names = {item["function"]["name"] for item in TOOL_DEFS}
        assert {"query_history", "set_essence", "react_emoji", "send_poke"} <= names
        assert {"tool_help", "think", "remove_essence"}.isdisjoint(names)

    def test_all_tool_names_unique(self):
        from src.core.llm.tools.providers.qq import TOOL_DEFS

        names = [t["function"]["name"] for t in TOOL_DEFS]
        assert len(names) == len(set(names))


class TestToolExecutorCalls:
    @pytest.mark.asyncio
    async def test_execute_get_current_time(self, bot):
        """get_current_time returns datetime string."""
        import re

        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        tc = ToolCall(id="call_0", name="get_current_time", arguments={})
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
        assert "datetime" in response_buf["results"][0]["result"]
        dt_str = response_buf["results"][0]["result"]["datetime"]
        assert isinstance(dt_str, str)
        assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", dt_str)

    @pytest.mark.asyncio
    async def test_execute_set_essence(self, bot):
        """set_essence tool calls bot.api.set_essence_msg."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        """query_history without args returns recent group messages with timestamps."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        # New format includes timestamp: [message_id] MM-DD HH:MM nickname(user_id): content
        assert "Alice" in text
        assert "hello" in text
        assert "Bob" in text
        assert "world" in text

    @pytest.mark.asyncio
    async def test_query_with_keyword(self, bot):
        """query_history with keyword filters by search."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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
        """query_history limit clamps at 50."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

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

        # Should not error, limit clamped to 50
        assert len(response_buf["results"]) == 1
        assert "result" in response_buf["results"][0]

    @pytest.mark.asyncio
    async def test_query_excludes_bot_messages(self, bot):
        """query_history bot_scope=exclude excludes bot's own messages."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot_qq = bot.config.bot.qq
        bot.msg_store.record_bot_message(1, 123, bot_qq, "Bot", "bot msg", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 100, "Alice", "user msg", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh7", "function": {"name": "query_history", "arguments": '{"bot_scope": "exclude"}'}}
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

    @pytest.mark.asyncio
    async def test_query_bot_scope_include(self, bot):
        """bot_scope=include returns bot messages alongside user messages."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot_qq = bot.config.bot.qq
        bot.msg_store.record_bot_message(1, 123, bot_qq, "Bot", "bot msg", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 100, "Alice", "user msg", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh8", "function": {"name": "query_history", "arguments": '{"bot_scope": "include"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "bot msg" in text
        assert "user msg" in text

    @pytest.mark.asyncio
    async def test_query_bot_scope_only(self, bot):
        """bot_scope=only returns only bot messages."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot_qq = bot.config.bot.qq
        bot.msg_store.record_bot_message(1, 123, bot_qq, "Bot", "bot msg", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 100, "Alice", "user msg", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh9", "function": {"name": "query_history", "arguments": '{"bot_scope": "only"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "bot msg" in text
        assert "user msg" not in text

    @pytest.mark.asyncio
    async def test_query_bot_scope_only_conflict(self, bot):
        """bot_scope=only + other user_id returns error."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh10", "function": {"name": "query_history", "arguments": '{"bot_scope": "only", "user_id": 100}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "参数冲突" in text

    @pytest.mark.asyncio
    async def test_query_keyword_and_user_id_combo(self, bot):
        """query_history with keyword+user_id uses AND semantics."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "hello", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "hello", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh11", "function": {"name": "query_history", "arguments": '{"keyword": "hello", "user_id": 100}'}}
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
    async def test_query_time_after(self, bot):
        """query_history with time_after filters messages."""
        from datetime import datetime

        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "old", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "new", 2000, True)

        # Compute local-time datetime string that round-trips to the Unix timestamp
        dt_after = (
            datetime.fromtimestamp(1500, tz=UTC)
            .astimezone()
            .replace(tzinfo=None)
            .strftime("%Y-%m-%d %H:%M:%S")
        )

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh12", "function": {"name": "query_history", "arguments": '{"time_after": "' + dt_after + '"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "new" in text
        assert "old" not in text

    @pytest.mark.asyncio
    async def test_query_time_before(self, bot):
        """query_history with time_before filters messages."""
        from datetime import datetime

        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "old", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "new", 2000, True)

        dt_before = (
            datetime.fromtimestamp(1500, tz=UTC)
            .astimezone()
            .replace(tzinfo=None)
            .strftime("%Y-%m-%d %H:%M:%S")
        )

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh13", "function": {"name": "query_history", "arguments": '{"time_before": "' + dt_before + '"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "old" in text
        assert "new" not in text

    @pytest.mark.asyncio
    async def test_query_private_with_keyword(self, bot):
        """query_history in private chat supports keyword."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot.msg_store.record_bot_message(1, None, 999, "User", "hello world", 1000, False)
        bot.msg_store.record_bot_message(2, None, 999, "User", "goodbye", 1001, False)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh14", "function": {"name": "query_history", "arguments": '{"keyword": "hello"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": None,
                "user_id": 999,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "hello" in text
        assert "goodbye" not in text

    @pytest.mark.asyncio
    async def test_query_private_with_time(self, bot):
        """query_history in private chat supports time_after."""
        from datetime import datetime

        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot.msg_store.record_bot_message(1, None, 999, "User", "old", 1000, False)
        bot.msg_store.record_bot_message(2, None, 999, "User", "new", 2000, False)

        dt_after = (
            datetime.fromtimestamp(1500, tz=UTC)
            .astimezone()
            .replace(tzinfo=None)
            .strftime("%Y-%m-%d %H:%M:%S")
        )

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh15", "function": {"name": "query_history", "arguments": '{"time_after": "' + dt_after + '"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": None,
                "user_id": 999,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "new" in text
        assert "old" not in text

    @pytest.mark.asyncio
    async def test_query_by_message_id(self, bot):
        """query_history with message_id returns the exact message."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "first", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "second", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh16", "function": {"name": "query_history", "arguments": '{"message_id": 2}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "second" in text
        assert "first" not in text

    @pytest.mark.asyncio
    async def test_query_message_id_and_keyword(self, bot):
        """query_history with message_id + keyword uses AND semantics."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "hello", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "hello", 1001, True)

        # message_id=1 AND keyword="hello" → match
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh17", "function": {"name": "query_history", "arguments": '{"message_id": 1, "keyword": "hello"}'}}
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

        # message_id=1 AND keyword="goodbye" → no match
        response_buf2: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh18", "function": {"name": "query_history", "arguments": '{"message_id": 1, "keyword": "goodbye"}'}}
                ],
                "response_buffer": response_buf2,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text2 = response_buf2["results"][0]["result"]["text"]
        assert "没有找到" in text2

    @pytest.mark.asyncio
    async def test_query_by_message_id_fallback(self, bot):
        """query_history falls back to get_msg when message_id not in local store."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot.api.get_msg_response = {
            "data": {
                "sender": {"user_id": 999, "nickname": "Remote", "card": ""},
                "message": "hello from remote",
                "time": 5000,
            }
        }
        async def _get_msg(msg_id):
            return bot.api.get_msg_response
        bot.api.get_msg = _get_msg

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh_fb", "function": {"name": "query_history", "arguments": '{"message_id": 99999}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "找到以下消息" in text
        assert "Remote" in text
        assert "hello from remote" in text

    @pytest.mark.asyncio
    async def test_query_default_include_bot_messages(self, bot):
        """query_history no-arg default (bot_scope=include) returns bot messages."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        bot_qq = bot.config.bot.qq
        bot.msg_store.record_bot_message(1, 123, bot_qq, "Bot", "bot msg", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 100, "Alice", "user msg", 1001, True)

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qh_include", "function": {"name": "query_history", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )

        text = response_buf["results"][0]["result"]["text"]
        assert "bot msg" in text
        assert "user msg" in text

    @pytest.mark.asyncio
    async def test_query_default_limit_30(self, bot):
        """query_history no-arg uses default limit=30 without exclude_user_ids."""
        from unittest.mock import patch

        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        with patch.object(bot.msg_store, "query", wraps=bot.msg_store.query) as mock_query:
            await llm_tools_handler(
                {
                    "llm_type": "tool",
                    "tool_calls": [
                        {"id": "qh_limit", "function": {"name": "query_history", "arguments": "{}"}}
                    ],
                    "response_buffer": response_buf,
                    "group_id": 123,
                    "user_id": 111,
                },
                bot,
            )

        call_kwargs = mock_query.call_args.kwargs
        assert call_kwargs["n"] == 30
        assert "exclude_user_ids" not in call_kwargs

    @pytest.mark.asyncio
    async def test_query_explicit_limit(self, bot):
        """query_history with explicit limit is clamped at 50."""
        from unittest.mock import patch

        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        with patch.object(bot.msg_store, "query", wraps=bot.msg_store.query) as mock_query:
            await llm_tools_handler(
                {
                    "llm_type": "tool",
                    "tool_calls": [
                        {"id": "qh_lim", "function": {"name": "query_history", "arguments": '{"limit": 5}'}}
                    ],
                    "response_buffer": response_buf,
                    "group_id": 123,
                    "user_id": 111,
                },
                bot,
            )

        assert mock_query.call_args.kwargs["n"] == 5


# 方向一: 信息获取工具
# ====================================================================


class TestGetGroupInfo:
    @pytest.mark.asyncio
    async def test_basic(self, bot):
        """get_group_info returns key group fields."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_get_group_info(group_id):
            bot.api.calls.append({"method": "get_group_info", "group_id": group_id})
            return {
                "data": {
                    "group_name": "Test Group",
                    "group_id": 123,
                    "member_count": 50,
                    "max_member_count": 200,
                    "owner_id": 100,
                    "group_memo": "welcome",
                }
            }

        bot.api.get_group_info = fake_get_group_info
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "g1", "function": {"name": "get_group_info", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        result = response_buf["results"][0]["result"]
        assert "Test Group" in result["text"]
        assert "50" in result["text"]
        assert "welcome" in result["text"]

    @pytest.mark.asyncio
    async def test_empty_data(self, bot):
        """get_group_info with empty response returns raw data as JSON."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_get_group_info(group_id):
            bot.api.calls.append({"method": "get_group_info", "group_id": group_id})
            return {}

        bot.api.get_group_info = fake_get_group_info
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "g2", "function": {"name": "get_group_info", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        assert "text" in response_buf["results"][0]["result"]


class TestGetMemberInfo:
    @pytest.mark.asyncio
    async def test_basic(self, bot):
        """get_member_info returns identity fields for a member."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_get_member_info(group_id, user_id):
            bot.api.calls.append(
                {"method": "get_group_member_info", "group_id": group_id, "user_id": user_id}
            )
            return {
                "data": {
                    "user_id": 555,
                    "nickname": "Alice",
                    "card": "A酱",
                    "role": "admin",
                    "title": "专属头衔",
                }
            }

        bot.api.get_group_member_info = fake_get_member_info
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "m1", "function": {"name": "get_member_info", "arguments": '{"user_id": 555}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        result = response_buf["results"][0]["result"]
        assert "Alice" in result["text"]
        assert "admin" in result["text"]
        assert "A酱" in result["text"]

    @pytest.mark.asyncio
    async def test_empty_data(self, bot):
        """get_member_info with empty response still returns valid JSON."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_get_member_info(group_id, user_id):
            bot.api.calls.append(
                {"method": "get_group_member_info", "group_id": group_id, "user_id": user_id}
            )
            return {}

        bot.api.get_group_member_info = fake_get_member_info
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "m2", "function": {"name": "get_member_info", "arguments": '{"user_id": 555}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        assert "text" in response_buf["results"][0]["result"]


class TestGetMemberList:
    @pytest.mark.asyncio
    async def test_list_format(self, bot):
        """get_member_list returns formatted member list with role tags."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_get_member_list(group_id):
            bot.api.calls.append({"method": "get_group_member_list", "group_id": group_id})
            return {
                "data": [
                    {"user_id": 100, "nickname": "Owner", "card": "", "role": "owner"},
                    {"user_id": 200, "nickname": "Admin", "card": "管理员", "role": "admin"},
                    {"user_id": 300, "nickname": "Member", "card": "", "role": "member"},
                ]
            }

        bot.api.get_group_member_list = fake_get_member_list
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "ml1", "function": {"name": "get_member_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "群成员共 3 人" in text
        assert "Owner" in text
        assert "[群主]" in text
        assert "[管理员]" in text
        assert "群名片:管理员" in text

    @pytest.mark.asyncio
    async def test_empty(self, bot):
        """get_member_list with empty data shows 0 members."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_get_member_list(group_id):
            bot.api.calls.append({"method": "get_group_member_list", "group_id": group_id})
            return {"data": []}

        bot.api.get_group_member_list = fake_get_member_list
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "ml2", "function": {"name": "get_member_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "群成员共 0 人" in text

    @pytest.mark.asyncio
    async def test_dict_with_members_key(self, bot):
        """get_member_list handles response with members key."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_get_member_list(group_id):
            bot.api.calls.append({"method": "get_group_member_list", "group_id": group_id})
            return {"data": {"members": [{"user_id": 100, "nickname": "Test", "card": "", "role": "member"}]}}

        bot.api.get_group_member_list = fake_get_member_list
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "ml3", "function": {"name": "get_member_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "Test" in text

    @pytest.mark.asyncio
    async def test_truncation_100(self, bot):
        """get_member_list truncates at 100 members."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        members = [
            {"user_id": i, "nickname": f"User{i}", "card": "", "role": "member"}
            for i in range(150)
        ]

        async def fake_get_member_list(group_id):
            bot.api.calls.append({"method": "get_group_member_list", "group_id": group_id})
            return {"data": members}

        bot.api.get_group_member_list = fake_get_member_list
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "ml4", "function": {"name": "get_member_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "共 150 人" in text
        assert "仅显示前100人" in text
        # Verify only 100 lines (not all 150 user IDs)
        assert "User0" in text
        assert "User99" in text
        assert "User100" not in text


class TestGetEssenceList:
    @pytest.mark.asyncio
    async def test_with_data(self, bot):
        """get_essence_list returns formatted essence messages."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_call_action(action, **params):
            bot.api.calls.append({"method": "call_action", "action": action, "params": params})
            if action == "get_essence_msg_list":
                return {
                    "data": [
                        {
                            "message_id": 1,
                            "sender_nick": "Alice",
                            "content": "hello world",
                            "time": 1000,
                        },
                        {
                            "message_id": 2,
                            "sender_nick": "Bob",
                            "content": "nice",
                            "time": 2000,
                        },
                    ]
                }
            return {}

        bot.api.call_action = fake_call_action
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "e1", "function": {"name": "get_essence_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "精华消息共 2 条" in text
        assert "Alice" in text
        assert "hello world" in text
        assert "Bob" in text

    @pytest.mark.asyncio
    async def test_empty(self, bot):
        """get_essence_list with empty data returns提示."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_call_action(action, **params):
            bot.api.calls.append({"method": "call_action", "action": action, "params": params})
            return {"data": []}

        bot.api.call_action = fake_call_action
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "e2", "function": {"name": "get_essence_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "暂无精华消息" in text

    @pytest.mark.asyncio
    async def test_truncation_20(self, bot):
        """get_essence_list truncates at 20 entries."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        essences = [
            {"message_id": i, "sender_nick": f"User{i}", "content": f"msg{i}"}
            for i in range(25)
        ]

        async def fake_call_action(action, **params):
            bot.api.calls.append({"method": "call_action", "action": action, "params": params})
            return {"data": essences}

        bot.api.call_action = fake_call_action
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "e3", "function": {"name": "get_essence_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "共 25 条" in text
        assert "仅展示前20条" in text
        assert "msg0" in text
        assert "msg19" in text
        assert "msg20" not in text

    @pytest.mark.asyncio
    async def test_dict_list_content(self, bot):
        """get_essence_list handles list-type message content."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_call_action(action, **params):
            bot.api.calls.append({"method": "call_action", "action": action, "params": params})
            return {
                "data": [
                    {
                        "message_id": 1,
                        "sender_nick": "Alice",
                        "content": [
                            {"type": "text", "data": {"text": "hello"}},
                            {"type": "image", "data": {}},
                            {"type": "text", "data": {"text": " world"}},
                        ],
                    }
                ]
            }

        bot.api.call_action = fake_call_action
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "e4", "function": {"name": "get_essence_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "hello" in text
        assert "world" in text


class TestGetShutList:
    @pytest.mark.asyncio
    async def test_with_data(self, bot):
        """get_shut_list returns formatted shut-up list."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_call_action(action, **params):
            bot.api.calls.append({"method": "call_action", "action": action, "params": params})
            if action == "get_group_shut_list":
                return {
                    "data": [
                        {"user_id": 555, "nickname": "BadUser", "duration": 300},
                        {"user_id": 666, "nickname": "Spammer", "duration": 600},
                    ]
                }
            return {}

        bot.api.call_action = fake_call_action
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "s1", "function": {"name": "get_shut_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "禁言列表共 2 人" in text
        assert "BadUser" in text
        assert "剩余 300 秒" in text
        assert "Spammer" in text

    @pytest.mark.asyncio
    async def test_empty(self, bot):
        """get_shut_list with empty data returns提示."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        async def fake_call_action(action, **params):
            bot.api.calls.append({"method": "call_action", "action": action, "params": params})
            return {"data": []}

        bot.api.call_action = fake_call_action
        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "s2", "function": {"name": "get_shut_list", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        text = response_buf["results"][0]["result"]["text"]
        assert "没有成员被禁言" in text


# ====================================================================
# 方向二: 群管理工具
# ====================================================================


class TestSetGroupCard:
    @pytest.mark.asyncio
    async def test_basic(self, bot):
        """set_group_card calls call_action with correct params."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "sc1", "function": {"name": "set_group_card", "arguments": '{"user_id": 555, "card": "新名片"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        # Verify call_action was invoked
        call_action_calls = [
            c for c in bot.api.calls if c.get("method") == "call_action"
        ]
        assert len(call_action_calls) == 1
        assert call_action_calls[0]["action"] == "set_group_card"
        assert call_action_calls[0]["params"]["group_id"] == 123
        assert call_action_calls[0]["params"]["user_id"] == 555
        assert call_action_calls[0]["params"]["card"] == "新名片"

    @pytest.mark.asyncio
    async def test_empty_card(self, bot):
        """set_group_card with empty card string clears the card."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "sc2", "function": {"name": "set_group_card", "arguments": '{"user_id": 555, "card": ""}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert calls[0]["params"]["card"] == ""


class TestDeleteMsg:
    @pytest.mark.asyncio
    async def test_basic(self, bot):
        """delete_msg calls call_action with delete_msg action."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "d1", "function": {"name": "delete_msg", "arguments": '{"message_id": 12345}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert len(calls) == 1
        assert calls[0]["action"] == "delete_msg"
        assert calls[0]["params"]["message_id"] == 12345


class TestWholeBan:
    @pytest.mark.asyncio
    async def test_enable(self, bot):
        """whole_ban enable=true calls set_group_whole_ban."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "wb1", "function": {"name": "whole_ban", "arguments": '{"enable": true}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert len(calls) == 1
        assert calls[0]["action"] == "set_group_whole_ban"
        assert calls[0]["params"]["enable"] is True

    @pytest.mark.asyncio
    async def test_disable(self, bot):
        """whole_ban enable=false calls set_group_whole_ban with enable=False."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "wb2", "function": {"name": "whole_ban", "arguments": '{"enable": false}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert calls[0]["params"]["enable"] is False

    @pytest.mark.asyncio
    async def test_default_enable(self, bot):
        """whole_ban without enable defaults to True."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "wb3", "function": {"name": "whole_ban", "arguments": "{}"}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert calls[0]["params"]["enable"] is True


class TestSetAdmin:
    @pytest.mark.asyncio
    async def test_set_admin(self, bot):
        """set_admin enable=true calls set_group_admin."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "sa1", "function": {"name": "set_admin", "arguments": '{"user_id": 555, "enable": true}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert len(calls) == 1
        assert calls[0]["action"] == "set_group_admin"
        assert calls[0]["params"]["user_id"] == 555
        assert calls[0]["params"]["enable"] is True

    @pytest.mark.asyncio
    async def test_unset_admin(self, bot):
        """set_admin enable=false unsets admin."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "sa2", "function": {"name": "set_admin", "arguments": '{"user_id": 555, "enable": false}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert calls[0]["params"]["enable"] is False

    @pytest.mark.asyncio
    async def test_default_enable(self, bot):
        """set_admin without enable defaults to True."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "sa3", "function": {"name": "set_admin", "arguments": '{"user_id": 555}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert calls[0]["params"]["enable"] is True


# ====================================================================
# 方向三: 互动与内容感知
# ====================================================================


class TestSendPoke:
    @pytest.mark.asyncio
    async def test_basic(self, bot):
        """send_poke calls NapCat's unified group_poke action."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "p1", "function": {"name": "send_poke", "arguments": '{"user_id": 555}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [
            c for c in bot.api.calls
            if c.get("method") == "call_action" and c.get("action") == "group_poke"
        ]
        assert len(calls) == 1
        assert calls[0]["params"]["group_id"] == 123
        assert calls[0]["params"]["user_id"] == 555


class TestSendLike:
    @pytest.mark.asyncio
    async def test_basic(self, bot):
        """send_like with explicit times calls call_action."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "l1", "function": {"name": "send_like", "arguments": '{"user_id": 999, "times": 5}'}}
                ],
                "response_buffer": response_buf,
                "group_id": None,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert len(calls) == 1
        assert calls[0]["action"] == "send_like"
        assert calls[0]["params"]["user_id"] == 999
        assert calls[0]["params"]["times"] == 5

    @pytest.mark.asyncio
    async def test_default_times(self, bot):
        """send_like without times defaults to 1."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "l2", "function": {"name": "send_like", "arguments": '{"user_id": 999}'}}
                ],
                "response_buffer": response_buf,
                "group_id": None,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert calls[0]["params"]["times"] == 1

    @pytest.mark.asyncio
    async def test_private_context(self, bot):
        """send_like works in private chat context (no group_id)."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "l3", "function": {"name": "send_like", "arguments": '{"user_id": 999}'}}
                ],
                "response_buffer": response_buf,
                "group_id": None,
                "user_id": 999,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert len(calls) == 1
        assert calls[0]["params"]["user_id"] == 999


class TestOcrImage:
    @pytest.mark.asyncio
    async def test_basic(self, bot):
        """ocr_image calls call_action with ocr_image action."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "o1", "function": {"name": "ocr_image", "arguments": '{"image": "http://example.com/img.png"}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert len(calls) == 1
        assert calls[0]["action"] == "ocr_image"
        assert calls[0]["params"]["image"] == "http://example.com/img.png"


class TestVoiceToText:
    @pytest.mark.asyncio
    async def test_basic(self, bot):
        """voice_to_text calls call_action with fetch_ptt_text action."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "v1", "function": {"name": "voice_to_text", "arguments": '{"message_id": 12345}'}}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        calls = [c for c in bot.api.calls if c.get("method") == "call_action"]
        assert len(calls) == 1
        assert calls[0]["action"] == "fetch_ptt_text"
        assert calls[0]["params"]["message_id"] == 12345


# ====================================================================
# 方向四: Agent 认知增强
# ====================================================================


    def test_tool_definition_exists(self):
        """query_character should be in TOOL_DEFS."""
        from src.core.llm.tools.providers.qq import TOOL_DEFS

        names = [td["function"]["name"] for td in TOOL_DEFS]
        assert "query_character" in names

    def test_query_by_character_name(self):
        """Query by a character name returns their description paragraph."""
        from src.core.llm.tools.providers import qq as lt

        lt._CHARACTER_SECTIONS = None
        lt._CHARACTER_FULL_TEXT = None

        result = lt._query_character("辛美尔")
        assert "辛美尔" in result["text"]
        assert "Himmel" in result["text"]

    def test_query_by_section_title(self):
        """Query by section title returns that section."""
        from src.core.llm.tools.providers.qq import _query_character

        result = _query_character("魔法介绍")
        assert len(result["text"]) > 200
        assert "魔法" in result["text"]

    def test_query_no_match(self):
        """Unknown keyword returns helpful error message."""
        from src.core.llm.tools.providers.qq import _query_character

        result = _query_character("不存在的关键词xyz")
        assert "未找到" in result["text"]

    def test_truncate_long_content(self):
        """Long content should be truncated with a note."""
        from src.core.llm.tools.providers.qq import _truncate_content

        result = _truncate_content("x" * 2000)
        assert len(result) <= 1600
        assert "已截断" in result

    def test_schema_describes_query(self):
        from src.core.llm.tools.providers.qq import TOOL_DEFS

        schema = next(
            item["function"]
            for item in TOOL_DEFS
            if item["function"]["name"] == "query_character"
        )
        assert "人物设定" in schema["description"]
        assert "keyword" in schema["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_execute_integration(self, bot):
        """query_character via _execute dispatch returns expected content."""
        from tests.tool_runner import execute_tool_calls as llm_tools_handler

        response_buf: dict = {}
        await llm_tools_handler(
            {
                "llm_type": "tool",
                "tool_calls": [
                    {"id": "qc1", "function": {
                        "name": "query_character",
                        "arguments": '{"keyword": "芙莉莲"}',
                    }}
                ],
                "response_buffer": response_buf,
                "group_id": 123,
                "user_id": 111,
            },
            bot,
        )
        result = response_buf["results"][0]["result"]
        assert "text" in result
        assert "芙莉莲" in result["text"]
