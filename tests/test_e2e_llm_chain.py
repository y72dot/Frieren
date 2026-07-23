"""E2E LLM chain tests: gate → core → tools → sender pipeline."""

from __future__ import annotations

import time

import pytest

from src.core.config import LLMConfig
from src.core.llm import LlmResponse, ToolCall
from src.core.message_bus import MessageType
from tests.conftest_e2e import (
    FakeLlmProvider,
    assert_api_called,
    dispatch_raw_event,
    e2e_bot,  # noqa: F401
    e2e_llm_bot,  # noqa: F401
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider_responses(bot, responses: list[LlmResponse]) -> FakeLlmProvider:
    """Replace bot.llm_provider with a fresh FakeLlmProvider set to *responses*."""
    provider = FakeLlmProvider()
    provider.responses = responses
    bot.llm_provider = provider
    return provider


def _raw_group_at_msg(user_id=111, text="Hello bot", group_id=456, msg_id=1) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "user_id": user_id,
        "group_id": group_id,
        "raw_message": f"[CQ:at,qq=123456] {text}",
        "message_id": msg_id,
        "time": int(time.time()),
        "sender": {"nickname": "Alice", "card": ""},
    }


def _raw_private_msg(user_id=789, text="hello", msg_id=1) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": user_id,
        "raw_message": text,
        "message_id": msg_id,
        "time": int(time.time()),
        "sender": {"nickname": "Bob"},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLLMChainBasic:
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_simple_text_reply(self, e2e_llm_bot):  # noqa: F811
        """@bot → chat_completion returns text → llm_sender sends it."""
        _make_provider_responses(e2e_llm_bot, [LlmResponse(text="Hello Alice!")])

        raw = _raw_group_at_msg()
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert_api_called(
            e2e_llm_bot, "send_group_msg", group_id=456, message="Hello Alice!"
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_tool_call_then_reply(self, e2e_llm_bot):  # noqa: F811
        """@bot → first round tool call → second round text reply."""
        _make_provider_responses(
            e2e_llm_bot,
            [
                LlmResponse(
                    tool_calls=[ToolCall(id="c1", name="get_current_time", arguments={})]
                ),
                LlmResponse(text="The time is now."),
            ],
        )

        raw = _raw_group_at_msg()
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert_api_called(
            e2e_llm_bot, "send_group_msg", group_id=456, message="The time is now."
        )
        # Verify LLM was called twice
        assert len(e2e_llm_bot.llm_provider.calls) == 2

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_multiple_tools_one_turn(self, e2e_llm_bot):  # noqa: F811
        """One LLM turn returns multiple tool calls → all executed → final reply."""
        _make_provider_responses(
            e2e_llm_bot,
            [
                LlmResponse(
                    tool_calls=[
                        ToolCall(id="c1", name="get_current_time", arguments={}),
                        ToolCall(
                            id="c2",
                            name="query_history",
                            arguments={"limit": 1},
                        ),
                    ]
                ),
                LlmResponse(text="Done."),
            ],
        )

        raw = _raw_group_at_msg()
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert_api_called(
            e2e_llm_bot, "send_group_msg", group_id=456, message="Done."
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_tool_across_multiple_turns(self, e2e_llm_bot):  # noqa: F811
        """T1 tool → T2 tool → T3 text (multi-turn tool chain)."""
        _make_provider_responses(
            e2e_llm_bot,
            [
                LlmResponse(
                    tool_calls=[ToolCall(id="c1", name="get_current_time", arguments={})]
                ),
                LlmResponse(
                    tool_calls=[
                        ToolCall(
                            id="c2",
                            name="query_history",
                            arguments={"limit": 1},
                        )
                    ]
                ),
                LlmResponse(text="All done."),
            ],
        )

        raw = _raw_group_at_msg()
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert_api_called(
            e2e_llm_bot, "send_group_msg", group_id=456, message="All done."
        )
        assert len(e2e_llm_bot.llm_provider.calls) == 3

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_empty_llm_response_skipped(self, e2e_llm_bot):  # noqa: F811
        """LLM returns empty text → no message is sent."""
        _make_provider_responses(e2e_llm_bot, [LlmResponse(text="")])

        raw = _raw_group_at_msg()
        await dispatch_raw_event(e2e_llm_bot, raw)

        # No send_group_msg should have been called for an empty reply
        send_calls = [
            c
            for c in e2e_llm_bot.api.calls
            if c.get("method") == "send_group_msg"
        ]
        assert len(send_calls) == 0


class TestLLMChainRouting:
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_group_vs_private_routing(self, e2e_llm_bot):  # noqa: F811
        """Group chat → send_group_msg; private chat → send_private_msg."""
        _make_provider_responses(
            e2e_llm_bot, [LlmResponse(text="Group reply"), LlmResponse(text="Private reply")]
        )

        # Group message
        raw_group = _raw_group_at_msg(msg_id=1)
        await dispatch_raw_event(e2e_llm_bot, raw_group)

        assert_api_called(
            e2e_llm_bot, "send_group_msg", group_id=456, message="Group reply"
        )

        # Reset for private
        _make_provider_responses(
            e2e_llm_bot, [LlmResponse(text="Private reply")]
        )
        raw_private = _raw_private_msg(msg_id=2)
        await dispatch_raw_event(e2e_llm_bot, raw_private)

        assert_api_called(
            e2e_llm_bot, "send_private_msg", user_id=789, message="Private reply"
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_llm_disabled_ignores(self, e2e_bot):  # noqa: F811
        """When config.llm.enabled=False, gate returns False and LLM is not called."""
        from plugins.llm_gate import LlmGatePlugin

        e2e_bot.config.llm = LLMConfig(
            enabled=False,
            api_base="https://fake.example.com",
            api_key="sk-fake",
            model="fake",
            max_turns=3,
        )
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, LlmGatePlugin(), 5)

        provider = _make_provider_responses(e2e_bot, [LlmResponse(text="Should not send")])

        raw = _raw_group_at_msg()
        await dispatch_raw_event(e2e_bot, raw)

        # LLM provider should NOT have been called
        assert len(provider.calls) == 0

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_self_message_ignored(self, e2e_llm_bot):  # noqa: F811
        """Bot's own message is not processed by LLM gate."""
        provider = _make_provider_responses(
            e2e_llm_bot, [LlmResponse(text="Should not send")]
        )

        raw = _raw_group_at_msg(user_id=123456)  # bot's own QQ
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert len(provider.calls) == 0


class TestLLMChainSession:
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_session_cache_reuse(self, e2e_llm_bot):  # noqa: F811
        """Same session_key in TTL → appends user message instead of creating new."""
        _make_provider_responses(
            e2e_llm_bot,
            [LlmResponse(text="Reply 1"), LlmResponse(text="Reply 2")],
        )

        # First message creates new session
        raw1 = _raw_group_at_msg(msg_id=1, text="First message")
        await dispatch_raw_event(e2e_llm_bot, raw1)

        # Session should exist in cache
        assert "group:456" in e2e_llm_bot.session_mgr._cache

        # Second message reuses session (TTL=3600 by default, so cache is fresh)
        raw2 = _raw_group_at_msg(msg_id=2, text="Second message")
        await dispatch_raw_event(e2e_llm_bot, raw2)

        assert_api_called(e2e_llm_bot, "send_group_msg", message="Reply 2")

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_long_reply_chunking(self, e2e_llm_bot):  # noqa: F811
        """Reply >4000 chars → llm_sender splits into multiple chunks."""
        long_text = "A" * 5000
        _make_provider_responses(e2e_llm_bot, [LlmResponse(text=long_text)])

        raw = _raw_group_at_msg()
        await dispatch_raw_event(e2e_llm_bot, raw)

        send_calls = [
            c
            for c in e2e_llm_bot.api.calls
            if c.get("method") == "send_group_msg"
        ]
        # Should be at least 2 chunks
        assert len(send_calls) >= 2
        # Total content across chunks should equal the original
        total = "".join(c["message"] for c in send_calls)
        assert total == long_text
