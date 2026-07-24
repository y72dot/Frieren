"""Tests for MessageBus: subscribe, dispatch, emit, flush, suppression."""

from typing import Any

import pytest

from src.core.config import (
    BotConfig,
    BotConfigSection,
    FilterConfig,
    FilterModeConfig,
    LoggingConfigSection,
    NapCatConfig,
    PluginConfig,
    PluginFilterConfig,
)
from src.core.filter_manager import FilterManager
from src.core.message_bus import BusMessage, MessageBus, MessageType
from src.core.message_store import MessageStore
from src.plugin.base import Event
from src.plugin.bridge import _MiddlewarePipelineAdapter
from src.plugin.middleware import MiddlewarePipeline

# -------------------------------------------------------------------
# minimal bot stub
# -------------------------------------------------------------------


class _MinimalBot:
    """Bot stub exposing message_bus and api for bus tests."""

    def __init__(self, bus: MessageBus):
        self.message_bus = bus
        self.msg_store = MessageStore(db_path=":memory:")
        self.api = _MinimalApi()
        self.filter_mgr = FilterManager()


class _MinimalApi:
    """Api stub that records raw calls."""

    def __init__(self):
        self.calls: list[dict] = []

    async def _raw_call(self, action: str, **params):
        self.calls.append({"action": action, **params})
        return {"status": "ok", "action": action}


# -------------------------------------------------------------------
# plugin stubs
# -------------------------------------------------------------------


class _EchoPlugin:
    name = "echo"
    priority = 10

    def match(self, event: Event) -> bool:
        return event.message.startswith("/echo")

    async def handle(self, event: Event, bot) -> bool:
        return True


class _PingPlugin:
    name = "ping"
    priority = 0

    def match(self, event: Event) -> bool:
        return event.message.startswith("/ping")

    async def handle(self, event: Event, bot) -> bool:
        return True


class _PassThroughPlugin:
    name = "passthrough"
    priority = 5

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot) -> bool:
        return False  # never consume


class _SpamFilter:
    name = "spam_filter"
    priority = 0

    def match(self, payload) -> bool:
        return payload.get("action", "") == "send_group_msg"

    async def handle(self, payload, bot) -> bool:
        return "spam" in str(payload.get("message", ""))


# -------------------------------------------------------------------
# subscribe / subscription count
# -------------------------------------------------------------------


def test_subscribe_adds_handler():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    assert bus.subscription_count == 1


def test_subscribe_duplicate_replaces():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 10)
    assert bus.subscription_count == 1


def test_unsubscribe_removes():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    assert bus.unsubscribe(MessageType.EXTERNAL, "ping") is True
    assert bus.subscription_count == 0


def test_unsubscribe_missing():
    bus = MessageBus()
    assert bus.unsubscribe(MessageType.EXTERNAL, "nope") is False


# -------------------------------------------------------------------
# dispatch – EXTERNAL (suppressible)
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_external_consumed():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    bot = _MinimalBot(bus)

    event = Event(type="message.group", user_id=1, message="/ping")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)
    assert result is True


@pytest.mark.asyncio
async def test_dispatch_external_not_consumed():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    bot = _MinimalBot(bus)

    event = Event(type="message.group", user_id=1, message="/help")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)
    assert result is False


@pytest.mark.asyncio
async def test_dispatch_external_priority_order():
    """Lower priority runs first. Ping (pri=0) should consume before Echo (pri=10)."""
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _EchoPlugin(), 10)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    bot = _MinimalBot(bus)

    event = Event(type="message.group", user_id=1, message="/ping is also echo-like")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)
    # Ping runs first and returns True → consumed
    assert result is True


@pytest.mark.asyncio
async def test_dispatch_external_passthrough():
    """A non-consuming plugin should let lower-priority plugins run."""
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PassThroughPlugin(), 5)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 10)
    bot = _MinimalBot(bus)

    event = Event(type="message.group", user_id=1, message="/ping")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)
    assert result is True  # ping eventually consumed


# -------------------------------------------------------------------
# dispatch – ACTION (suppressible)
# -------------------------------------------------------------------


class _ActionTerminal:
    """Simple ACTION terminal handler for tests."""

    name = "action_terminal"
    priority = 100

    def match(self, payload) -> bool:
        return isinstance(payload, dict) and "action" in payload

    async def handle(self, payload, bot) -> Any:
        action = payload["action"]
        params = {k: v for k, v in payload.items() if k != "action"}
        return await bot.api._raw_call(action, **params)


@pytest.mark.asyncio
async def test_dispatch_action_terminal():
    """ACTION messages should be handled by a registered terminal handler."""
    bus = MessageBus()
    bus.subscribe(MessageType.ACTION, _ActionTerminal(), 100)
    bot = _MinimalBot(bus)

    msg = BusMessage(
        type=MessageType.ACTION,
        payload={"action": "send_group_msg", "group_id": 123, "message": "hello"},
    )
    result = await bus.dispatch(msg, bot)
    assert result == {"status": "ok", "action": "send_group_msg"}
    assert len(bot.api.calls) == 1
    assert bot.api.calls[0]["action"] == "send_group_msg"


@pytest.mark.asyncio
async def test_dispatch_action_spam_suppressed():
    """A spam filter (pri=0) should suppress spam messages before terminal."""
    bus = MessageBus()
    bus.subscribe(MessageType.ACTION, _SpamFilter(), 0)
    bus.subscribe(MessageType.ACTION, _ActionTerminal(), 100)
    bot = _MinimalBot(bus)

    msg = BusMessage(
        type=MessageType.ACTION,
        payload={
            "action": "send_group_msg",
            "group_id": 123,
            "message": "buy spam now!!!",
        },
    )
    result = await bus.dispatch(msg, bot)
    assert result is True  # suppressed by spam filter
    assert len(bot.api.calls) == 0  # terminal never called


@pytest.mark.asyncio
async def test_dispatch_action_spam_filter_passes():
    """Non-spam messages should pass through the filter to the terminal."""
    bus = MessageBus()
    bus.subscribe(MessageType.ACTION, _SpamFilter(), 0)
    bus.subscribe(MessageType.ACTION, _ActionTerminal(), 100)
    bot = _MinimalBot(bus)

    msg = BusMessage(
        type=MessageType.ACTION,
        payload={"action": "send_group_msg", "group_id": 123, "message": "hello world"},
    )
    result = await bus.dispatch(msg, bot)
    assert result == {"status": "ok", "action": "send_group_msg"}
    assert len(bot.api.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_action_empty_terminal_result_is_still_consumed():
    """An empty successful API result must not fall through to _qq_exec."""
    bus = MessageBus()
    terminal_calls = 0

    async def empty_terminal(action, params):
        nonlocal terminal_calls
        terminal_calls += 1
        return None

    adapter = _MiddlewarePipelineAdapter(MiddlewarePipeline([], empty_terminal))
    bus.subscribe(MessageType.ACTION, adapter, 0)
    bot = _MinimalBot(bus)

    msg = BusMessage(
        type=MessageType.ACTION,
        payload={"action": "group_poke", "group_id": 123, "user_id": 456},
    )
    result = await bus.dispatch(msg, bot)

    assert result is None
    assert terminal_calls == 1
    assert bot.api.calls == []


# -------------------------------------------------------------------
# dispatch – INTERNAL (non-suppressible)
# -------------------------------------------------------------------


class _InternalListener:
    def __init__(self, name: str):
        self.name = name
        self.priority = 0
        self.received: list = []

    def match(self, payload):
        return True

    async def handle(self, payload, bot):
        self.received.append(payload)
        return True  # True should be ignored for INTERNAL


@pytest.mark.asyncio
async def test_dispatch_internal_all_run():
    """INTERNAL messages should run all subscribers regardless of return value."""
    bus = MessageBus()
    a = _InternalListener("a")
    b = _InternalListener("b")
    bus.subscribe(MessageType.INTERNAL, a, 0)
    bus.subscribe(MessageType.INTERNAL, b, 10)
    bot = _MinimalBot(bus)

    msg = BusMessage(type=MessageType.INTERNAL, payload={"event": "test"})
    result = await bus.dispatch(msg, bot)
    assert result is None
    assert len(a.received) == 1
    assert len(b.received) == 1


# -------------------------------------------------------------------
# dispatch – LIFECYCLE (non-suppressible)
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_lifecycle_all_run():
    """LIFECYCLE messages should run all subscribers."""
    bus = MessageBus()
    a = _InternalListener("a")
    b = _InternalListener("b")
    bus.subscribe(MessageType.LIFECYCLE, a, 0)
    bus.subscribe(MessageType.LIFECYCLE, b, 10)
    bot = _MinimalBot(bus)

    msg = BusMessage(type=MessageType.LIFECYCLE, payload={"event": "startup"})
    result = await bus.dispatch(msg, bot)
    assert result is None
    assert len(a.received) == 1
    assert len(b.received) == 1


# -------------------------------------------------------------------
# emit / flush
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_and_flush():
    """Emitted messages should be processed during flush."""
    bus = MessageBus()
    bus.subscribe(MessageType.ACTION, _ActionTerminal(), 100)
    bot = _MinimalBot(bus)

    bus.emit(
        BusMessage(
            type=MessageType.ACTION,
            payload={"action": "send_group_msg", "group_id": 1, "message": "hi"},
        )
    )
    assert len(bot.api.calls) == 0  # not yet processed

    await bus.flush(bot)
    assert len(bot.api.calls) == 1
    assert bot.api.calls[0]["action"] == "send_group_msg"


@pytest.mark.asyncio
async def test_emit_and_wait():
    """emit_and_wait should process immediately, not through the queue."""
    bus = MessageBus()
    bus.subscribe(MessageType.ACTION, _ActionTerminal(), 100)
    bot = _MinimalBot(bus)

    msg = BusMessage(
        type=MessageType.ACTION,
        payload={"action": "send_group_msg", "group_id": 1, "message": "hi"},
    )
    result = await bus.emit_and_wait(msg, bot)
    assert result == {"status": "ok", "action": "send_group_msg"}
    assert len(bot.api.calls) == 1


# -------------------------------------------------------------------
# depth limiting
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_depth_limit():
    """Messages with depth > 10 should be dropped."""
    bus = MessageBus()
    bot = _MinimalBot(bus)

    msg = BusMessage(
        type=MessageType.EXTERNAL, payload=Event(type="x", user_id=1), depth=11
    )
    result = await bus.dispatch(msg, bot)
    assert result is False


# -------------------------------------------------------------------
# clear
# -------------------------------------------------------------------


def test_clear_resets_subscriptions():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    assert bus.subscription_count == 1

    bus.clear()
    assert bus.subscription_count == 0
    assert len(bus._queue) == 0


# -------------------------------------------------------------------
# dispatch – global filter blocking
# -------------------------------------------------------------------


class _CountingPlugin:
    """A plugin that records whether it was invoked."""

    def __init__(self, name: str, priority: int = 0):
        self.name = name
        self.priority = priority
        self.match_called = False
        self.handle_called = False

    def match(self, event: Event) -> bool:
        self.match_called = True
        return True

    async def handle(self, event: Event, bot) -> bool:
        self.handle_called = True
        return True


def _make_filter_config(**kwargs) -> BotConfig:
    return BotConfig(
        bot=BotConfigSection(
            qq=123456, nickname=[], admin_users=kwargs.pop("admin_users", [])
        ),
        napcat=NapCatConfig(),
        plugin=PluginConfig(),
        logging=LoggingConfigSection(),
        filter=FilterConfig(**kwargs),
    )


@pytest.mark.asyncio
async def test_global_filter_blocks_before_plugins():
    """When global filter blocks, no plugin should see the event."""
    bus = MessageBus()
    plugin = _CountingPlugin("echo")
    bus.subscribe(MessageType.EXTERNAL, plugin, 0)
    bot = _MinimalBot(bus)

    cfg = _make_filter_config(
        group=FilterModeConfig(mode="blacklist", list=[100]),
    )
    bot.filter_mgr = FilterManager(cfg)

    event = Event(
        type="message.group", user_id=1, message="hi", group_id=100, is_group=True
    )
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)

    assert result is False
    assert plugin.match_called is False
    assert plugin.handle_called is False


@pytest.mark.asyncio
async def test_global_filter_passes_to_plugins():
    """When global filter does not block, plugins should run."""
    bus = MessageBus()
    plugin = _CountingPlugin("echo")
    bus.subscribe(MessageType.EXTERNAL, plugin, 0)
    bot = _MinimalBot(bus)

    cfg = _make_filter_config(
        group=FilterModeConfig(mode="blacklist", list=[200]),  # only blocks group 200
    )
    bot.filter_mgr = FilterManager(cfg)

    event = Event(
        type="message.group", user_id=1, message="hi", group_id=100, is_group=True
    )
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)

    assert result is True
    assert plugin.match_called is True
    assert plugin.handle_called is True


# -------------------------------------------------------------------
# dispatch – per-plugin filter blocking
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_filter_skips_blocked_plugin():
    """A plugin-level filter should skip the blocked plugin but let others run."""
    bus = MessageBus()
    blocked = _CountingPlugin("blocked", priority=0)
    unblocked = _CountingPlugin("unblocked", priority=10)
    bus.subscribe(MessageType.EXTERNAL, blocked, 0)
    bus.subscribe(MessageType.EXTERNAL, unblocked, 10)
    bot = _MinimalBot(bus)

    cfg = _make_filter_config(
        plugins={
            "blocked": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            ),
        },
    )
    bot.filter_mgr = FilterManager(cfg)

    event = Event(
        type="message.group", user_id=1, message="hi", group_id=100, is_group=True
    )
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)

    assert result is True  # consumed by unblocked
    assert blocked.match_called is False
    assert blocked.handle_called is False
    assert unblocked.match_called is True
    assert unblocked.handle_called is True


@pytest.mark.asyncio
async def test_plugin_filter_not_configured_plugin_runs():
    """A plugin without filter config should run normally."""
    bus = MessageBus()
    plugin = _CountingPlugin("echo")
    bus.subscribe(MessageType.EXTERNAL, plugin, 0)
    bot = _MinimalBot(bus)

    cfg = _make_filter_config(
        plugins={
            "other": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            ),
        },
    )
    bot.filter_mgr = FilterManager(cfg)

    event = Event(
        type="message.group", user_id=1, message="hi", group_id=100, is_group=True
    )
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)

    assert result is True
    assert plugin.match_called is True
    assert plugin.handle_called is True


@pytest.mark.asyncio
async def test_admin_bypasses_plugin_filter_in_dispatch():
    """Admin should bypass plugin-level filters during dispatch."""
    bus = MessageBus()
    plugin = _CountingPlugin("echo")
    bus.subscribe(MessageType.EXTERNAL, plugin, 0)
    bot = _MinimalBot(bus)

    cfg = _make_filter_config(
        admin_users=[555],
        plugins={
            "echo": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            ),
        },
    )
    bot.filter_mgr = FilterManager(cfg)

    event = Event(
        type="message.group", user_id=555, message="hi", group_id=100, is_group=True
    )
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)

    assert result is True
    assert plugin.match_called is True
    assert plugin.handle_called is True


# -------------------------------------------------------------------
# SubscriptionScope – scoped bulk unsubscribe
# -------------------------------------------------------------------


def test_scope_subscribe_and_close():
    """Subscriptions made through a scope should be removed on close."""
    bus = MessageBus()
    scope = bus.create_scope("test_plugin", generation=1)

    count_before = bus.subscription_count

    plugin = _PingPlugin()
    bus.subscribe(MessageType.EXTERNAL, plugin, 0, scope=scope)
    assert bus.subscription_count == count_before + 1

    scope.close()
    assert bus.subscription_count == count_before  # all scope subs removed


def test_scope_close_idempotent():
    """Closing a scope twice should not raise or double-unsubscribe."""
    bus = MessageBus()
    scope = bus.create_scope("test_plugin", generation=1)

    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0, scope=scope)
    count_after_sub = bus.subscription_count

    scope.close()
    assert bus.subscription_count == count_after_sub - 1

    # Second close should be a no-op
    scope.close()
    assert bus.subscription_count == count_after_sub - 1


def test_scope_multiple_subscriptions():
    """A scope should track subscriptions across multiple message types."""
    bus = MessageBus()
    scope = bus.create_scope("multi", generation=1)

    ping = _PingPlugin()
    listener = _InternalListener("listener_a")

    count_before = bus.subscription_count
    bus.subscribe(MessageType.EXTERNAL, ping, 0, scope=scope)
    bus.subscribe(MessageType.INTERNAL, listener, 10, scope=scope)

    assert bus.subscription_count == count_before + 2

    scope.close()
    assert bus.subscription_count == count_before  # both removed


def test_scope_no_cross_interference():
    """Closing one scope should not affect subscriptions in another scope."""
    bus = MessageBus()
    scope_a = bus.create_scope("plugin_a", generation=1)
    scope_b = bus.create_scope("plugin_b", generation=1)

    ping = _PingPlugin()
    echo = _EchoPlugin()

    count_before = bus.subscription_count
    bus.subscribe(MessageType.EXTERNAL, ping, 0, scope=scope_a)
    bus.subscribe(MessageType.EXTERNAL, echo, 10, scope=scope_b)
    assert bus.subscription_count == count_before + 2

    scope_a.close()
    # Only ping should be removed
    assert bus.subscription_count == count_before + 1

    # Verify echo is still present
    subs = bus._subscriptions[MessageType.EXTERNAL]
    names = [s.handler.name for s in subs]
    assert "echo" in names
    assert "ping" not in names

    scope_b.close()
    assert bus.subscription_count == count_before


@pytest.mark.asyncio
async def test_scope_closed_handler_not_dispatched():
    """After a scope is closed, its handlers should no longer receive events."""
    bus = MessageBus()
    scope = bus.create_scope("temp", generation=1)

    plugin = _CountingPlugin("temp_counter")
    bus.subscribe(MessageType.EXTERNAL, plugin, 0, scope=scope)

    scope.close()

    from src.plugin.base import Event

    event = Event(type="message.group", user_id=1, message="hello")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)

    class _Bot:
        def __init__(self, bus_obj):
            self.message_bus = bus_obj
            self.filter_mgr = FilterManager()

    await bus.dispatch(msg, _Bot(bus))
    assert plugin.match_called is False
    assert plugin.handle_called is False


def test_subscribe_without_scope_still_works():
    """Backward compatibility: subscribing without a scope works as before."""
    bus = MessageBus()
    count_before = bus.subscription_count

    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)  # no scope
    assert bus.subscription_count == count_before + 1
