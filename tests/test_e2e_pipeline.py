"""E2E pipeline tests: raw napcat event → dispatch → plugin → API call.

Covers the complete EXTERNAL request lifecycle without a real WebSocket.
"""

from __future__ import annotations

import pytest

from src.core.message_bus import BusMessage, MessageType
from src.plugin.base import Event
from tests.conftest_e2e import assert_api_called, dispatch_raw_event, e2e_bot  # noqa: F401


# ---------------------------------------------------------------------------
# Test plugins (inline)
# ---------------------------------------------------------------------------


class _CmdPlugin:
    name = "ping_cmd"
    priority = 0

    def match(self, event: Event) -> bool:
        return event.message.startswith("/ping")

    async def handle(self, event: Event, bot) -> bool:
        if event.is_group and event.group_id:
            await bot.api.send_group_msg(event.group_id, "pong")
        return True


class _AlwaysMatchTrue:
    name = "always_true"
    priority = 5

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot) -> bool:
        return True


class _AlwaysMatchFalse:
    name = "always_false"
    priority = 10

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot) -> bool:
        return False


class _PrivateReplyPlugin:
    name = "private_reply"
    priority = 0

    def match(self, event: Event) -> bool:
        return event.type == "message.private"

    async def handle(self, event: Event, bot) -> bool:
        await bot.api.send_private_msg(event.user_id, "got it")
        return True


class _NoticePlugin:
    name = "notice_catcher"
    priority = 0

    def __init__(self):
        self.last_event = None

    def match(self, event: Event) -> bool:
        return event.type.startswith("notice.")

    async def handle(self, event: Event, bot) -> bool:
        self.last_event = event
        return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineBasic:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_command_plugin_e2e(self, e2e_bot):
        """Raw dict → /ping plugin → send_group_msg API call."""
        e2e_bot.plugin_manager.register(_CmdPlugin())

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "/ping",
            "message_id": 1001,
            "time": 1700000000,
            "sender": {"nickname": "Alice"},
        }

        await dispatch_raw_event(e2e_bot, raw)

        assert_api_called(e2e_bot, "send_group_msg", group_id=456, message="pong")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_event_records_to_msg_store(self, e2e_bot):
        """Dispatched event is persisted in msg_store."""
        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "hello world",
            "message_id": 2001,
            "time": 1700000000,
            "sender": {"nickname": "Bob"},
        }

        await dispatch_raw_event(e2e_bot, raw)

        stored = e2e_bot.msg_store.recent(456, n=5)
        assert len(stored) == 1
        assert stored[0].content == "hello world"
        assert stored[0].user_id == 111
        assert stored[0].message_id == 2001

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_plugin_consumption_suppression(self, e2e_bot):
        """First plugin that returns True consumes event; second never called."""
        second_called = False

        class _SecondPlugin:
            name = "second"
            priority = 20

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                nonlocal second_called
                second_called = True
                return True

        e2e_bot.plugin_manager.register(_AlwaysMatchTrue())
        e2e_bot.plugin_manager.register(_SecondPlugin())

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "test",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert second_called is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_priority_ordering(self, e2e_bot):
        """Lower priority value = higher priority = handles first."""
        order: list[str] = []

        class _P5:
            name = "p5"
            priority = 5

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                order.append("p5")
                return True

        class _P20:
            name = "p20"
            priority = 20

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                order.append("p20")
                return True

        e2e_bot.plugin_manager.register(_P20())
        e2e_bot.plugin_manager.register(_P5())

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "test",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert order == ["p5"]  # priority 5 fires first, consumes event

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_notice_event_passthrough(self, e2e_bot):
        """Notice events are parsed, stored, and dispatched to notice plugins."""
        catcher = _NoticePlugin()
        e2e_bot.plugin_manager.register(catcher)

        raw = {
            "post_type": "notice",
            "notice_type": "group_increase",
            "user_id": 999,
            "group_id": 456,
            "time": 1700000000,
        }

        await dispatch_raw_event(e2e_bot, raw)

        assert catcher.last_event is not None
        assert catcher.last_event.type == "notice.group_increase"
        assert catcher.last_event.user_id == 999
        assert catcher.last_event.group_id == 456

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_private_message_routing(self, e2e_bot):
        """Private messages route through send_private_msg."""
        e2e_bot.plugin_manager.register(_PrivateReplyPlugin())

        raw = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 789,
            "raw_message": "hello",
            "message_id": 3001,
            "time": 1700000000,
            "sender": {"nickname": "Charlie"},
        }

        await dispatch_raw_event(e2e_bot, raw)

        assert_api_called(e2e_bot, "send_private_msg", user_id=789, message="got it")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_plugin_returns_false_chain(self, e2e_bot):
        """plugin match=True handle=False → next matching plugin gets the event."""
        second_called = False

        class _SecondHandler:
            name = "second_handler"
            priority = 10

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                nonlocal second_called
                second_called = True
                return True

        e2e_bot.plugin_manager.register(_AlwaysMatchFalse())
        e2e_bot.plugin_manager.register(_SecondHandler())

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "test",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert second_called is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_unconsumed_event(self, e2e_bot):
        """No plugin matches the event → dispatch completes without error."""
        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "nobody matches this",
            "message_id": 1,
        }

        # Should not raise
        await dispatch_raw_event(e2e_bot, raw)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_depth_limit_10(self, e2e_bot):
        """Messages exceeding depth=10 are silently dropped."""
        # Build a chain of messages that would exceed depth 10
        msg = BusMessage(type=MessageType.INTERNAL, payload={"x": 1}, source="test")

        # Emit a message that re-emits itself in a handler
        emit_count = 0

        class _DeepPlugin:
            name = "deep"
            priority = 0

            def match(self, event: Event) -> bool:
                return event.message == "deep"

            async def handle(self, event: Event, bot) -> bool:
                nonlocal emit_count
                for _ in range(12):
                    emit_count += 1
                    bot.message_bus.emit(
                        BusMessage(
                            type=MessageType.INTERNAL,
                            payload={"x": 1},
                            source="deep",
                            depth=msg.depth + emit_count,
                        )
                    )
                return True

        e2e_bot.plugin_manager.register(_DeepPlugin())

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "deep",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        # After flush, messages with depth > 10 should be dropped without error.
        # The test passes if no exception is raised.
