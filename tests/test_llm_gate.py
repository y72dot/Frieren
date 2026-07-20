"""Tests for llm_gate – @bot detection and trigger emission."""

from __future__ import annotations

import pytest

from plugins.llm_gate import LlmGatePlugin
from src.core.message_bus import MessageType
from src.plugin.base import Event


class TestLlmGateMatch:
    @pytest.fixture
    def gate(self):
        return LlmGatePlugin()

    def test_match_group_at_bot(self, gate):
        event = Event(
            type="message.group",
            user_id=111,
            group_id=456,
            is_group=True,
            message="[CQ:at,qq=123456] 你好",
        )
        assert gate.match(event) is True

    def test_match_private(self, gate):
        event = Event(
            type="message.private",
            user_id=111,
            is_group=False,
            message="hello",
        )
        assert gate.match(event) is True

    def test_no_match_group_without_at(self, gate):
        event = Event(
            type="message.group",
            user_id=111,
            group_id=456,
            is_group=True,
            message="hello",
        )
        assert gate.match(event) is False

    def test_no_match_other_event_type(self, gate):
        event = Event(
            type="notice.group_increase",
            user_id=111,
            group_id=456,
            is_group=True,
            message="",
        )
        assert gate.match(event) is False


class TestLlmGateHandle:
    @pytest.fixture
    def gate(self):
        return LlmGatePlugin()

    @pytest.mark.asyncio
    async def test_handle_disabled_llm(self, gate, bot):
        """Returns False when LLM is disabled."""
        bot.config.llm.enabled = False
        event = Event(
            type="message.group",
            user_id=111,
            group_id=456,
            is_group=True,
            message="[CQ:at,qq=123456] 你好",
        )
        result = await gate.handle(event, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_no_llm_provider(self, gate, bot):
        """Returns False when llm_provider is None."""
        bot.config.llm.enabled = True
        bot.llm_provider = None
        event = Event(
            type="message.group",
            user_id=111,
            group_id=456,
            is_group=True,
            message="[CQ:at,qq=123456] 你好",
        )
        result = await gate.handle(event, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_self_message(self, gate, bot):
        """Bot's own messages should be ignored."""
        bot.config.llm.enabled = True
        bot.llm_provider = object()  # non-None
        event = Event(
            type="message.group",
            user_id=bot.config.bot.qq,
            group_id=456,
            is_group=True,
            message="[CQ:at,qq=123456] 你好",
        )
        result = await gate.handle(event, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_empty_after_cq_strip(self, gate, bot):
        """Returns False when only CQ codes remain (no plain text)."""
        bot.config.llm.enabled = True
        bot.llm_provider = object()
        event = Event(
            type="message.group",
            user_id=111,
            group_id=456,
            is_group=True,
            message="[CQ:at,qq=123456]",
        )
        result = await gate.handle(event, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_emits_trigger(self, gate, bot):
        """Successful gate emits an INTERNAL trigger and returns True."""
        bot.config.llm.enabled = True
        bot.llm_provider = object()
        event = Event(
            type="message.group",
            user_id=111,
            group_id=456,
            is_group=True,
            message="[CQ:at,qq=123456] 你好世界",
        )

        # Set up a spy on the bus to capture emitted messages
        emitted = []

        async def spy_dispatch(msg, _bot):
            emitted.append(msg)
            return None

        original = bot.message_bus.emit_and_wait
        bot.message_bus.emit_and_wait = spy_dispatch

        try:
            result = await gate.handle(event, bot)
        finally:
            bot.message_bus.emit_and_wait = original

        assert result is True
        assert len(emitted) == 1
        msg = emitted[0]
        assert msg.type == MessageType.INTERNAL
        assert msg.payload["llm_type"] == "trigger"
        assert msg.payload["session_key"] == "group:456"
        assert msg.payload["text"] == "你好世界"
        assert msg.payload["user_id"] == 111
        assert msg.payload["is_group"] is True
