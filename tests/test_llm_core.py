"""Tests for llm_core – main LLM agent loop and orchestration."""

from __future__ import annotations

import pytest

from src.core.llm import LlmResponse


class TestLlmCoreHandler:
    @pytest.mark.asyncio
    async def test_no_match(self, bot):
        """Returns False for non-trigger llm_type payloads."""
        from plugins.llm_core import llm_core_handler

        result = await llm_core_handler({"llm_type": "other"}, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_simple_text_response(self, bot_with_llm):
        """Single-turn text response flow."""
        from plugins.llm_core import _lazy_init, llm_core_handler

        # Pre-init tool registry
        _lazy_init(bot_with_llm)

        # Set up LLM to return a single text response
        provider = bot_with_llm.llm_provider
        provider.responses = [LlmResponse(text="你好，有什么可以帮你吗？")]

        # Register llm_tools and llm_sender handlers on the bus
        from plugins.llm_sender import llm_sender_handler
        from plugins.llm_tools import llm_tools_handler
        from src.core.message_bus import MessageType

        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_sender_handler, "llm_sender", 40), 40
        )
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_tools_handler, "llm_tools", 30), 30
        )

        result = await llm_core_handler(
            {
                "llm_type": "trigger",
                "session_key": "group:123",
                "user_id": 111,
                "group_id": 123,
                "is_group": True,
                "text": "你好",
                "nickname": "测试用户",
            },
            bot_with_llm,
        )

        assert result is False
        # Verify LLM was called
        assert len(provider.calls) == 1
        # Verify reply was sent
        send_calls = [
            c for c in bot_with_llm.api.calls
            if c.get("method") == "send_group_msg"
        ]
        assert len(send_calls) == 1
        assert send_calls[0]["group_id"] == 123
        assert "你好" in send_calls[0]["message"]

    @pytest.mark.asyncio
    async def test_multi_turn_with_tool_calls(self, bot_with_llm):
        """Multi-turn flow: tool call then text response."""
        from plugins.llm_core import _lazy_init, llm_core_handler

        _lazy_init(bot_with_llm)

        from src.core.llm import ToolCall

        provider = bot_with_llm.llm_provider
        provider.responses = [
            # Turn 1: tool call
            LlmResponse(
                tool_calls=[
                    ToolCall(
                        id="call_abc",
                        name="send_message",
                        arguments={"text": "好的，我这就设精！"},
                    )
                ]
            ),
            # Turn 2: final text
            LlmResponse(text="已发送通知，还有什么需要帮助的吗？"),
        ]

        from plugins.llm_sender import llm_sender_handler
        from plugins.llm_tools import llm_tools_handler
        from src.core.message_bus import MessageType

        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_sender_handler, "llm_sender", 40), 40
        )
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_tools_handler, "llm_tools", 30), 30
        )

        result = await llm_core_handler(
            {
                "llm_type": "trigger",
                "session_key": "group:123",
                "user_id": 111,
                "group_id": 123,
                "is_group": True,
                "text": "设精最后一条消息",
                "nickname": "测试用户",
            },
            bot_with_llm,
        )

        assert result is False
        # 2 LLM calls: tool + final text
        assert len(provider.calls) == 2
        # Tool was executed (send_message called)
        send_calls = [
            c for c in bot_with_llm.api.calls
            if c.get("method") == "send_group_msg"
        ]
        assert len(send_calls) >= 1
        assert any("好的，我这就设精" in c.get("message", "") for c in send_calls)

    @pytest.mark.asyncio
    async def test_tool_call_to_sender_fallback(self, bot_with_llm):
        """When LLM returns tool_calls repeatedly, max_turns triggers final reply."""
        from plugins.llm_core import _lazy_init, llm_core_handler

        _lazy_init(bot_with_llm)

        from src.core.llm import ToolCall

        provider = bot_with_llm.llm_provider
        # Always return tool calls – max_turns=3 in test config
        tool_response = LlmResponse(
            tool_calls=[
                ToolCall(
                    id="call_loop",
                    name="send_message",
                    arguments={"text": "loop"},
                )
            ]
        )
        # After 3 tool call turns, the fallback (no tools) call returns text
        provider.responses = [tool_response] * 3 + [LlmResponse(text="够了")]

        from plugins.llm_sender import llm_sender_handler
        from plugins.llm_tools import llm_tools_handler
        from src.core.message_bus import MessageType

        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_sender_handler, "llm_sender", 40), 40
        )
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_tools_handler, "llm_tools", 30), 30
        )

        result = await llm_core_handler(
            {
                "llm_type": "trigger",
                "session_key": "group:123",
                "user_id": 111,
                "group_id": 123,
                "is_group": True,
                "text": "loop test",
                "nickname": "测试用户",
            },
            bot_with_llm,
        )

        assert result is False
        # All 4 calls made: 3 turns with tools + 1 fallback
        assert len(provider.calls) == 4

    @pytest.mark.asyncio
    async def test_empty_reply_not_sent(self, bot_with_llm):
        """Empty LLM responses are not forwarded to sender."""
        from plugins.llm_core import _lazy_init, llm_core_handler

        _lazy_init(bot_with_llm)

        provider = bot_with_llm.llm_provider
        provider.responses = [LlmResponse(text="")]

        from plugins.llm_sender import llm_sender_handler
        from plugins.llm_tools import llm_tools_handler
        from src.core.message_bus import MessageType

        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_sender_handler, "llm_sender", 40), 40
        )
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_tools_handler, "llm_tools", 30), 30
        )

        await llm_core_handler(
            {
                "llm_type": "trigger",
                "session_key": "group:123",
                "user_id": 111,
                "group_id": 123,
                "is_group": True,
                "text": "hi",
                "nickname": "测试用户",
            },
            bot_with_llm,
        )

        # No send calls because reply was empty
        send_calls = [
            c for c in bot_with_llm.api.calls
            if c.get("method") == "send_group_msg"
        ]
        assert len(send_calls) == 0


class TestSessionCache:
    @pytest.mark.asyncio
    async def test_session_reuse_within_ttl(self, bot_with_llm):
        """Same session_key within TTL reuses messages list."""
        from plugins.llm_core import _lazy_init, _session_cache, llm_core_handler

        _session_cache.clear()
        _lazy_init(bot_with_llm)

        provider = bot_with_llm.llm_provider
        provider.responses = [
            LlmResponse(text="first reply"),
            LlmResponse(text="second reply"),
        ]

        from plugins.llm_sender import llm_sender_handler
        from plugins.llm_tools import llm_tools_handler
        from src.core.message_bus import MessageType

        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_sender_handler, "llm_sender", 40), 40
        )
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_tools_handler, "llm_tools", 30), 30
        )

        payload = {
            "llm_type": "trigger",
            "session_key": "group:123",
            "user_id": 111,
            "group_id": 123,
            "is_group": True,
            "text": "hello",
            "nickname": "测试用户",
        }

        # First call: creates fresh session [system, user]
        await llm_core_handler(payload, bot_with_llm)
        assert len(provider.calls) == 1
        assert len(provider.calls[0]["messages"]) == 2  # system + user

        # Second call: reuses session, appends new user message
        await llm_core_handler(payload, bot_with_llm)
        assert len(provider.calls) == 2
        # Should have system + previous assistant + new user (3 messages)
        assert len(provider.calls[1]["messages"]) >= 3

        # Session is still cached
        assert "group:123" in _session_cache

    @pytest.mark.asyncio
    async def test_session_ttl_zero_always_fresh(self, bot_with_llm):
        """session_ttl=0 disables cache, every call starts fresh."""
        from plugins.llm_core import _lazy_init, _session_cache, llm_core_handler

        _session_cache.clear()
        _lazy_init(bot_with_llm)

        bot_with_llm.config.llm.session_ttl = 0

        provider = bot_with_llm.llm_provider
        provider.responses = [
            LlmResponse(text="first"),
            LlmResponse(text="second"),
        ]

        from plugins.llm_sender import llm_sender_handler
        from plugins.llm_tools import llm_tools_handler
        from src.core.message_bus import MessageType

        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_sender_handler, "llm_sender", 40), 40
        )
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_tools_handler, "llm_tools", 30), 30
        )

        payload = {
            "llm_type": "trigger",
            "session_key": "group:123",
            "user_id": 111,
            "group_id": 123,
            "is_group": True,
            "text": "hello",
            "nickname": "测试用户",
        }

        await llm_core_handler(payload, bot_with_llm)
        await llm_core_handler(payload, bot_with_llm)

        # Both calls should be fresh sessions.
        # First call: no history in msg_store → 2 messages (system + user)
        # Second call: first call's message now in msg_store → auto_init injects
        # assistant tool_call + tool result → 4 messages
        assert len(provider.calls) == 2
        assert len(provider.calls[0]["messages"]) == 2
        assert len(provider.calls[1]["messages"]) == 4

    @pytest.mark.asyncio
    async def test_session_expired_after_ttl(self, bot_with_llm, monkeypatch):
        """Session entry older than TTL starts a fresh session."""
        from plugins.llm_core import _lazy_init, _session_cache, llm_core_handler

        _session_cache.clear()
        _lazy_init(bot_with_llm)

        provider = bot_with_llm.llm_provider
        provider.responses = [
            LlmResponse(text="first"),
            LlmResponse(text="second"),
        ]

        from plugins.llm_sender import llm_sender_handler
        from plugins.llm_tools import llm_tools_handler
        from src.core.message_bus import MessageType

        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_sender_handler, "llm_sender", 40), 40
        )
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL, _make_adapter(llm_tools_handler, "llm_tools", 30), 30
        )

        payload = {
            "llm_type": "trigger",
            "session_key": "group:123",
            "user_id": 111,
            "group_id": 123,
            "is_group": True,
            "text": "hello",
            "nickname": "测试用户",
        }

        # First call with real time
        await llm_core_handler(payload, bot_with_llm)
        assert len(provider.calls[0]["messages"]) == 2

        # Backdate the cache entry to simulate expiry
        _session_cache["group:123"] = (0, _session_cache["group:123"][1])

        # Second call: should see expired entry and start fresh.
        # First call's message is now in msg_store, so auto_init injects
        # assistant tool_call + tool result → 4 messages
        await llm_core_handler(payload, bot_with_llm)
        assert len(provider.calls) == 2
        assert len(provider.calls[1]["messages"]) == 4


# ---------------------------------------------------------------------------
# Helper: adapt a @subscribe function as a Plugin for bus subscription
# ---------------------------------------------------------------------------


def _make_adapter(func, name: str, priority: int):
    """Create a Plugin-compatible adapter from a @subscribe handler function."""

    class _Adapter:
        def __init__(self):
            self.name = name
            self.priority = priority

        def match(self, payload):
            return True

        async def handle(self, payload, bot):
            return await func(payload, bot)

    return _Adapter()
