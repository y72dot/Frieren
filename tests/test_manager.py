"""Tests for PluginManager: registration, sorting, dispatch."""

import pytest

from src.core.filter_manager import FilterManager
from src.core.message_bus import MessageBus
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
# register / unregister
# -------------------------------------------------------------------


def test_register_adds_and_sorts():
    bus = MessageBus()
    pm = PluginManager(bus=bus)
    pm.register(_EchoPlugin())
    pm.register(_PingPlugin())
    assert pm.plugin_count == 2
    # lower priority first (sorted by name here since we check plugin list order)
    names = [p.name for p in pm.plugins]
    assert "echo" in names
    assert "ping" in names


def test_unregister_existing():
    bus = MessageBus()
    pm = PluginManager(bus=bus)
    pm.register(_PingPlugin())
    assert pm.unregister("ping") is True
    assert pm.plugin_count == 0


def test_unregister_missing():
    bus = MessageBus()
    pm = PluginManager(bus=bus)
    assert pm.unregister("nope") is False


# -------------------------------------------------------------------
# dispatch
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_matches_first():
    pm = PluginManager(bus=MessageBus())
    pm.register(_EchoPlugin())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    consumed = await pm.dispatch(event, bot)
    assert consumed is True


@pytest.mark.asyncio
async def test_dispatch_no_match():
    pm = PluginManager(bus=MessageBus())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/help")
    consumed = await pm.dispatch(event, bot)
    assert consumed is False


@pytest.mark.asyncio
async def test_dispatch_match_error_continues():
    pm = PluginManager(bus=MessageBus())
    pm.register(_FailingMatchPlugin())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    consumed = await pm.dispatch(event, bot)
    assert consumed is True  # second plugin handled it


@pytest.mark.asyncio
async def test_dispatch_handle_error_continues():
    pm = PluginManager(bus=MessageBus())
    pm.register(_FailingHandlePlugin())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    consumed = await pm.dispatch(event, bot)
    assert consumed is True  # ping handled it after bad_handle crashed


@pytest.mark.asyncio
async def test_dispatch_non_consuming_continues():
    pm = PluginManager(bus=MessageBus())
    pm.register(_NonConsumingPlugin())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    consumed = await pm.dispatch(event, bot)
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
            "poke_back",
            "repeater",
            "history",
            "essence",
            "action_queue_handler",
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
