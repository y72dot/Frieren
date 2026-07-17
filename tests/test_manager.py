"""Tests for PluginManager: registration, sorting, dispatch."""

import pytest

from src.plugin.base import Event, Plugin
from src.plugin.manager import PluginManager


class _DummyBot:
    def __init__(self):
        self.plugin_manager = PluginManager()


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
    pm = PluginManager()
    pm.register(_EchoPlugin())
    pm.register(_PingPlugin())
    assert pm.plugin_count == 2
    # lower priority first
    assert pm.plugins[0].name == "ping"
    assert pm.plugins[1].name == "echo"


def test_unregister_existing():
    pm = PluginManager()
    pm.register(_PingPlugin())
    assert pm.unregister("ping") is True
    assert pm.plugin_count == 0


def test_unregister_missing():
    pm = PluginManager()
    assert pm.unregister("nope") is False


# -------------------------------------------------------------------
# dispatch
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_matches_first():
    pm = PluginManager()
    pm.register(_EchoPlugin())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    consumed = await pm.dispatch(event, bot)
    assert consumed is True


@pytest.mark.asyncio
async def test_dispatch_no_match():
    pm = PluginManager()
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/help")
    consumed = await pm.dispatch(event, bot)
    assert consumed is False


@pytest.mark.asyncio
async def test_dispatch_match_error_continues():
    pm = PluginManager()
    pm.register(_FailingMatchPlugin())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    consumed = await pm.dispatch(event, bot)
    assert consumed is True  # second plugin handled it


@pytest.mark.asyncio
async def test_dispatch_handle_error_continues():
    pm = PluginManager()
    pm.register(_FailingHandlePlugin())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    consumed = await pm.dispatch(event, bot)
    assert consumed is True  # ping handled it after bad_handle crashed


@pytest.mark.asyncio
async def test_dispatch_non_consuming_continues():
    pm = PluginManager()
    pm.register(_NonConsumingPlugin())
    pm.register(_PingPlugin())

    bot = _DummyBot()
    event = Event(type="message.group", user_id=1, message="/ping")
    consumed = await pm.dispatch(event, bot)
    assert consumed is True  # ping eventually consumed it
