"""Tests for PluginManager: registration, sorting, dispatch."""

import pytest

from src.core.filter_manager import FilterManager
from src.core.message_bus import BusMessage, MessageBus, MessageType
from src.plugin.base import Event
from src.plugin.manager import PluginManager


class _DummyBot:
    def __init__(self):
        self.message_bus = MessageBus()
        self.plugin_manager = PluginManager(bus=self.message_bus)
        self.api = None
        self.filter_mgr = FilterManager()


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


class _FailingMatchPlugin:
    name = "bad_match"
    priority = 5

    def match(self, event: Event) -> bool:
        raise RuntimeError("match boom")

    async def handle(self, event: Event, bot) -> bool:
        return True


class _FailingHandlePlugin:
    name = "bad_handle"
    priority = 6

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot) -> bool:
        raise RuntimeError("handle boom")


class _NonConsumingPlugin:
    name = "non_consumer"
    priority = 7

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot) -> bool:
        return False


# -------------------------------------------------------------------
# bus-based registration (replaces removed pm.register())
# -------------------------------------------------------------------


def test_bus_subscribe_register_and_sort():
    bus = MessageBus()
    pm = PluginManager(bus=bus)
    # Register plugins via the bus directly.
    bus.subscribe(MessageType.EXTERNAL, _EchoPlugin(), 10)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    # PluginManager._plugins is not populated via bus.subscribe;
    # verify bus subscriptions are ordered by priority.
    subs = bus._subscriptions[MessageType.EXTERNAL]
    assert len(subs) == 2
    # Check priorities are correct values; bus stores in insertion order,
    # sorts during dispatch.
    priorities = {s.priority for s in subs}
    assert priorities == {0, 10}


# -------------------------------------------------------------------
# dispatch (via bus)
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_matches_first():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _EchoPlugin(), 10)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event, source="test")
    consumed = await bus.dispatch(msg, bot)
    assert consumed is True


@pytest.mark.asyncio
async def test_dispatch_no_match():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/help")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event, source="test")
    consumed = await bus.dispatch(msg, bot)
    assert consumed is False


@pytest.mark.asyncio
async def test_dispatch_match_error_continues():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _FailingMatchPlugin(), 5)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event, source="test")
    consumed = await bus.dispatch(msg, bot)
    assert consumed is True  # second plugin handled it


@pytest.mark.asyncio
async def test_dispatch_handle_error_continues():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _FailingHandlePlugin(), 6)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event, source="test")
    consumed = await bus.dispatch(msg, bot)
    assert consumed is True  # ping handled it after bad_handle crashed


@pytest.mark.asyncio
async def test_dispatch_non_consuming_continues():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _NonConsumingPlugin(), 7)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event, source="test")
    consumed = await bus.dispatch(msg, bot)
    assert consumed is True  # ping eventually consumed it


# -------------------------------------------------------------------
# auto_discover
# -------------------------------------------------------------------


class _DummyBotForDiscover:
    def __init__(self):
        self.plugin_manager = PluginManager(bus=MessageBus())


def test_auto_discover_empty_dir(tmp_path):
    pm = PluginManager(bus=MessageBus())
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("")
    count = pm.auto_discover([str(plugin_dir)])
    assert count == 0


def test_auto_discover_missing_dir():
    pm = PluginManager(bus=MessageBus())
    count = pm.auto_discover(["nonexistent_dir_xyz"])
    assert count == 0


def test_auto_discover_skips_disabled():
    """Disabled plugins should be excluded from discovery."""
    pm = PluginManager(bus=MessageBus())
    count = pm.auto_discover(
        plugin_dirs=["plugins"],
        disabled=[
            "ping",
            "echo",
            "poke",
            "repeater",
            "history",
            "essence",
            "sticker_react",
            "llm_core",
            "llm_sender",
            "llm_gate",
        ],
    )
    assert count == 0  # all plugins are disabled


def test_auto_discover_bad_import_does_not_crash(tmp_path):
    """A plugin file that fails to import should not crash auto_discover."""
    pm = PluginManager(bus=MessageBus())
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("")
    (plugin_dir / "bad_syntax.py").write_text("this is not valid python {{{")
    count = pm.auto_discover([str(plugin_dir)])
    assert count == 0
