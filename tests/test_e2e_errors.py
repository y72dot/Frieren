"""E2E error handling and edge case tests."""

from __future__ import annotations

import time

import pytest

from src.core.config import (
    ActionQueueConfig,
    BotConfig,
    FilterConfig,
    FilterModeConfig,
)
from src.core.llm import ToolCall
from src.core.message_bus import BusMessage, MessageType
from src.plugin.base import Event
from tests.conftest_e2e import (
    FakeLlmProvider,
    dispatch_raw_event,
    e2e_bot,  # noqa: F401
    e2e_llm_bot,  # noqa: F401
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _run_tool(bot, tool_calls: list[ToolCall], group_id=456, user_id=111):
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


def _setup_action_queue_bot(bot, **aq_kwargs):
    """Register action_queue handler on bot's bus and configure it."""
    from plugins.action_queue import ActionQueueBusAdapter, reset_state  # noqa: F401

    reset_state()
    aq_config = ActionQueueConfig(**aq_kwargs)
    bot.config = BotConfig(
        bot=bot.config.bot,
        napcat=bot.config.napcat,
        plugin=bot.config.plugin,
        logging=bot.config.logging,
        action_queue=aq_config,
        filter=bot.config.filter,
        llm=bot.config.llm,
        env=bot.config.env,
    )
    adapter = ActionQueueBusAdapter(config=aq_config)
    bot.message_bus.subscribe(MessageType.ACTION, adapter, 1)
    return bot


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolErrors:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_api_failure_in_tool(self, e2e_bot):  # noqa: F811
        """API failure in a tool returns error dict, doesn't crash."""
        # Make set_group_ban raise
        async def failing_ban(*args, **kwargs):
            raise RuntimeError("API connection refused")

        e2e_bot.api.set_group_ban = failing_ban

        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="mute_user", arguments={"user_id": 999, "duration": 60})],
        )
        result = results[0]
        assert "error" in result["result"]
        assert "API connection refused" in result["result"]["error"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_llm_provider_raises(self, e2e_llm_bot):  # noqa: F811
        """LLM provider raising an exception is caught and logged."""
        async def failing_chat(*args, **kwargs):
            raise RuntimeError("LLM API down")

        provider = FakeLlmProvider()
        provider.chat_completion = failing_chat
        e2e_llm_bot.llm_provider = provider

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "[CQ:at,qq=123456] test",
            "message_id": 1,
            "time": int(time.time()),
            "sender": {"nickname": "Alice"},
        }

        # Should not raise
        await dispatch_raw_event(e2e_llm_bot, raw)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_malformed_raw_event(self, e2e_bot):  # noqa: F811
        """Unknown post_type → EventBus.parse returns None → event discarded."""
        raw = {
            "post_type": "unknown_type",
            "user_id": 111,
        }
        # Should not raise
        await dispatch_raw_event(e2e_bot, raw)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_tool_missing_required_arg(self, e2e_bot):  # noqa: F811
        """mute_user without user_id raises KeyError → captured as error result."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="mute_user", arguments={"duration": 60})],
        )
        result = results[0]
        assert "error" in result["result"]
        assert "user_id" in result["result"]["error"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_private_send_like_no_group_id(self, e2e_bot):  # noqa: F811
        """send_like in private context works without group_id."""
        results = await _run_tool(
            e2e_bot,
            [ToolCall(id="c1", name="send_like", arguments={"user_id": 789})],
            group_id=None,
            user_id=789,
        )
        assert results[0].get("result", {}).get("error") is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_whole_ban_default_enable(self, e2e_bot):  # noqa: F811
        """whole_ban without enable arg defaults to True."""
        results = await _run_tool(
            e2e_bot, [ToolCall(id="c1", name="whole_ban", arguments={})]
        )
        # Should not error
        assert "error" not in results[0]["result"]
        # Verify enable=True was sent
        assert any(
            c.get("method") == "call_action"
            and c.get("action") == "set_group_whole_ban"
            and c.get("params", {}).get("enable") is True
            for c in e2e_bot.api.calls
        )


class TestActionQueueErrors:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_action_queue_block_list(self, e2e_bot):  # noqa: F811
        """Blocked action (set_group_kick) is consumed and not executed."""
        _setup_action_queue_bot(e2e_bot, block_actions=["set_group_kick"])

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": "set_group_kick", "group_id": 456, "user_id": 999},
            source="test",
        )
        result = await e2e_bot.message_bus.emit_and_wait(msg, e2e_bot)

        # Blocked → consumed (True returned)
        assert result is True
        # _raw_call should NOT have been called
        raw_calls = [c for c in e2e_bot.api.calls if c.get("method") == "set_group_kick"]
        assert len(raw_calls) == 0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_action_queue_spam_dedup(self, e2e_bot):  # noqa: F811
        """Same send_group_msg within spam_window → second is deduplicated."""
        _setup_action_queue_bot(
            e2e_bot,
            spam_window=10.0,
            spam_actions=["send_group_msg"],
            global_rate=0,  # no rate limit to avoid delays
            group_cooldown=0,
        )

        payload = {"action": "send_group_msg", "group_id": 456, "message": "spam test"}
        msg1 = BusMessage(type=MessageType.ACTION, payload=payload, source="test")
        msg2 = BusMessage(type=MessageType.ACTION, payload=dict(payload), source="test")

        result1 = await e2e_bot.message_bus.emit_and_wait(msg1, e2e_bot)
        result2 = await e2e_bot.message_bus.emit_and_wait(msg2, e2e_bot)

        # First should pass through (returns API response dict)
        assert isinstance(result1, dict)
        # Second should be blocked (returns True)
        assert result2 is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_action_queue_rate_limit(self, e2e_bot):  # noqa: F811
        """Low global_rate causes delays between actions."""
        _setup_action_queue_bot(
            e2e_bot,
            global_rate=2.0,  # max 2 actions/sec = 500ms interval
            group_cooldown=0,
            spam_window=0,
        )

        payload1 = {"action": "send_group_msg", "group_id": 456, "message": "msg1"}
        payload2 = {"action": "send_group_msg", "group_id": 789, "message": "msg2"}

        msg1 = BusMessage(type=MessageType.ACTION, payload=payload1, source="test")
        msg2 = BusMessage(type=MessageType.ACTION, payload=payload2, source="test")

        t0 = time.monotonic()
        await e2e_bot.message_bus.emit_and_wait(msg1, e2e_bot)
        await e2e_bot.message_bus.emit_and_wait(msg2, e2e_bot)
        elapsed = time.monotonic() - t0

        # Both should succeed
        send_calls = [c for c in e2e_bot.api.calls if c.get("method") == "send_group_msg"]
        assert len(send_calls) >= 2
        # Rate limiting should have introduced at least some delay
        assert elapsed > 0.1  # at least noticeable delay


class TestFilterErrors:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_filter_blocks_entire_event(self, e2e_bot):  # noqa: F811
        """Blacklisted group → event blocked → no plugin executed."""

        e2e_bot.filter_mgr.update_config(
            BotConfig(
                bot=e2e_bot.config.bot,
                napcat=e2e_bot.config.napcat,
                plugin=e2e_bot.config.plugin,
                logging=e2e_bot.config.logging,
                filter=FilterConfig(
                    enable=True,
                    group=FilterModeConfig(mode="blacklist", list=[456]),
                ),
                env=e2e_bot.config.env,
            )
        )

        plugin_called = False

        class _P:
            name = "test_plugin"
            priority = 0

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                nonlocal plugin_called
                plugin_called = True
                return True

        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, _P(), 0)

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 999,
            "group_id": 456,
            "raw_message": "hello",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert plugin_called is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_missing_message_id_not_recorded(self, e2e_bot):  # noqa: F811
        """Event with message_id=None is not persisted to msg_store."""
        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "no id",
            # No message_id key → EventBus.parse gives message_id=None
            "time": int(time.time()),
            "sender": {"nickname": "Ghost"},
        }

        await dispatch_raw_event(e2e_bot, raw)
        stored = e2e_bot.msg_store.recent(456, n=5)
        assert len(stored) == 0
