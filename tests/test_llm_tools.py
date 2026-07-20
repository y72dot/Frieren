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

        assert len(TOOL_DEFS) == 8

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
    async def test_execute_get_current_time(self, bot):
        """get_current_time returns datetime string."""
        from plugins.llm_tools import llm_tools_handler
        import re

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
        """query_history without args returns recent group messages with timestamps."""
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
        # New format includes timestamp: [message_id] MM-DD HH:MM nickname(user_id): content
        assert "Alice" in text
        assert "hello" in text
        assert "Bob" in text
        assert "world" in text

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
        """query_history limit clamps at 50."""
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

        # Should not error, limit clamped to 50
        assert len(response_buf["results"]) == 1
        assert "result" in response_buf["results"][0]

    @pytest.mark.asyncio
    async def test_query_excludes_bot_messages(self, bot):
        """query_history default scope excludes bot's own messages."""
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

    @pytest.mark.asyncio
    async def test_query_bot_scope_include(self, bot):
        """bot_scope=include returns bot messages alongside user messages."""
        from plugins.llm_tools import llm_tools_handler

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
        from plugins.llm_tools import llm_tools_handler

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
        from plugins.llm_tools import llm_tools_handler

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
        from plugins.llm_tools import llm_tools_handler

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
        from plugins.llm_tools import llm_tools_handler
        from datetime import datetime, timezone

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "old", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "new", 2000, True)

        # Compute local-time datetime string that round-trips to the Unix timestamp
        dt_after = (
            datetime.fromtimestamp(1500, tz=timezone.utc)
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
        from plugins.llm_tools import llm_tools_handler
        from datetime import datetime, timezone

        bot.msg_store.record_bot_message(1, 123, 100, "Alice", "old", 1000, True)
        bot.msg_store.record_bot_message(2, 123, 200, "Bob", "new", 2000, True)

        dt_before = (
            datetime.fromtimestamp(1500, tz=timezone.utc)
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
        from plugins.llm_tools import llm_tools_handler

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
        from plugins.llm_tools import llm_tools_handler
        from datetime import datetime, timezone

        bot.msg_store.record_bot_message(1, None, 999, "User", "old", 1000, False)
        bot.msg_store.record_bot_message(2, None, 999, "User", "new", 2000, False)

        dt_after = (
            datetime.fromtimestamp(1500, tz=timezone.utc)
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
        from plugins.llm_tools import llm_tools_handler

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
        from plugins.llm_tools import llm_tools_handler

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
        from plugins.llm_tools import llm_tools_handler

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
