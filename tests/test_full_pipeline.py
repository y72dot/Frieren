"""End-to-end pipeline tests: raw event → dispatch → API call."""

from __future__ import annotations

import pytest

from src.core.event_bus import EventBus
from src.core.filter_manager import FilterManager
from src.core.message_bus import BusMessage, MessageBus, MessageType
from src.core.message_store import MessageStore
from src.plugin.base import Event
from src.plugin.manager import PluginManager

# -------------------------------------------------------------------
# stubs
# -------------------------------------------------------------------


class _PipelineApi:
    def __init__(self):
        self.calls: list[dict] = []

    async def _raw_call(self, action: str, **params):
        self.calls.append({"action": action, **params})
        return {"status": "ok", "action": action}


class _PipelineBot:
    def __init__(self, bus: MessageBus):
        self.message_bus = bus
        self.msg_store = MessageStore(db_path=":memory:")
        self.api = _PipelineApi()
        self.config = None
        self.filter_mgr = FilterManager()
        self.event_bus = EventBus()
        self.plugin_manager = PluginManager(bus=bus)


# -------------------------------------------------------------------
# full pipeline: raw event → plugin → API
# -------------------------------------------------------------------


class _EchoPlugin:
    name = "echo"
    priority = 10

    def match(self, event: Event) -> bool:
        return event.message.startswith("/echo")

    async def handle(self, event: Event, bot: _PipelineBot) -> bool:
        content = event.message.removeprefix("/echo").strip() or "empty"
        if event.is_group and event.group_id:
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": event.group_id,
                    "message": content,
                },
                source="echo",
            )
            bot.message_bus.emit(msg)
        return True


class _NoopPlugin:
    name = "noop"
    priority = 100

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot) -> bool:
        return False


class TestRawEventToApiCall:
    @pytest.mark.asyncio
    async def test_full_flow_group_message(self):
        """Raw dict event → parsed → dispatched → plugin handles → API called."""
        bus = MessageBus()
        bot = _PipelineBot(bus)
        bot.plugin_manager.register(_EchoPlugin())

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "/echo hello world",
            "message_id": 1001,
        }

        await bot.event_bus.dispatch(raw, bot)
        await bus.flush(bot)

        # Message should be stored
        stored = bot.msg_store.recent(456, n=5)
        assert len(stored) == 1
        assert stored[0].content == "/echo hello world"
        assert stored[0].user_id == 111

        # API should have been called
        send_calls = [c for c in bot.api.calls if c.get("action") == "send_group_msg"]
        assert len(send_calls) == 1
        assert send_calls[0]["group_id"] == 456
        assert send_calls[0]["message"] == "hello world"

    @pytest.mark.asyncio
    async def test_event_reaches_plugin_in_priority_order(self):
        """Higher priority plugin (lower number) handles event first."""
        bus = MessageBus()
        bot = _PipelineBot(bus)

        handled_by: list[str] = []

        class _Priority10:
            name = "p10"
            priority = 10

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                handled_by.append("p10")
                return True  # consume the event

        class _Priority50:
            name = "p50"
            priority = 50

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                handled_by.append("p50")
                return True

        bot.plugin_manager.register(_Priority10())
        bot.plugin_manager.register(_Priority50())

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "test",
            "message_id": 1,
        }

        await bot.event_bus.dispatch(raw, bot)

        assert handled_by == ["p10"]  # Only p10 fires, p50 never sees event


class TestFilterPipeline:
    @pytest.mark.asyncio
    async def test_global_whitelist_blocks_non_whitelisted(self):
        """Event from non-whitelisted group is blocked before plugins see it."""
        from src.core.config import (
            BotConfig,
            BotConfigSection,
            FilterConfig,
            FilterModeConfig,
            LoggingConfigSection,
            NapCatConfig,
            PluginConfig,
        )

        bus = MessageBus()
        bot = _PipelineBot(bus)

        # Set up whitelist filter that only allows group 999
        cfg = BotConfig(
            bot=BotConfigSection(qq=1, nickname=["bot"], admin_users=[]),
            napcat=NapCatConfig(),
            plugin=PluginConfig(),
            logging=LoggingConfigSection(),
            filter=FilterConfig(
                enable=True,
                group=FilterModeConfig(mode="whitelist", list=[999]),
            ),
            env={},
        )
        bot.filter_mgr.update_config(cfg)

        plugin_was_called = False

        class _TestPlugin:
            name = "test"
            priority = 0

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                nonlocal plugin_was_called
                plugin_was_called = True
                return True

        bot.plugin_manager.register(_TestPlugin())

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 123,  # not in whitelist
            "raw_message": "hello",
            "message_id": 1,
        }

        await bot.event_bus.dispatch(raw, bot)

        # Plugin should NOT have been called
        assert plugin_was_called is False


class TestLLMPipelineIntegration:
    @pytest.mark.asyncio
    async def test_llm_trigger_flows_through_bus(self, bot_with_llm):
        """LLM trigger INTERNAL message flows through gate→core→sender chain."""
        from plugins.llm_core import _lazy_init, llm_core_handler
        from plugins.llm_sender import llm_sender_handler
        from plugins.llm_tools import llm_tools_handler
        from src.core.llm import LlmResponse
        from src.core.message_bus import MessageType
        from src.plugin.manager import _SubscribeAdapter

        # Pre-init tool registry
        _lazy_init(bot_with_llm)

        # Set up LLM to return a text response
        provider = bot_with_llm.llm_provider
        provider.responses = [LlmResponse(text="Test reply")]

        # Register sender and tools on the bus
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL,
            _SubscribeAdapter(llm_sender_handler, "llm_sender", 40),
            40,
        )
        bot_with_llm.message_bus.subscribe(
            MessageType.INTERNAL,
            _SubscribeAdapter(llm_tools_handler, "llm_tools", 30),
            30,
        )

        # Dispatch a trigger
        result = await llm_core_handler(
            {
                "llm_type": "trigger",
                "session_key": "group:456",
                "user_id": 111,
                "group_id": 456,
                "is_group": True,
                "text": "Hello bot",
                "nickname": "Alice",
            },
            bot_with_llm,
        )

        assert result is False
        # LLM was called
        assert len(provider.calls) >= 1
        # Reply was sent via API
        send_calls = [
            c for c in bot_with_llm.api.calls
            if c.get("method") == "send_group_msg"
        ]
        assert len(send_calls) >= 1
