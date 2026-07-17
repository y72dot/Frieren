"""Tests for EventBus: event parsing and routing."""

import pytest

from src.core.event_bus import EventBus
from src.plugin.base import Event


class _DummyBot:
    def __init__(self):
        from src.plugin.manager import PluginManager
        self.plugin_manager = PluginManager()
        self._consumed_events: list[Event] = []

    async def handle(self, event, bot):
        self._consumed_events.append(event)
        return True


# -------------------------------------------------------------------
# dict-style message events
# -------------------------------------------------------------------


def test_parse_dict_group_message():
    bus = EventBus()
    raw = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 123,
        "group_id": 456,
        "raw_message": "/ping",
    }
    event = bus.parse(raw)
    assert event is not None
    assert event.type == "message.group"
    assert event.user_id == 123
    assert event.group_id == 456
    assert event.is_group is True
    assert event.message == "/ping"


def test_parse_dict_private_message():
    bus = EventBus()
    raw = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 789,
        "raw_message": "hello",
    }
    event = bus.parse(raw)
    assert event is not None
    assert event.type == "message.private"
    assert event.is_group is False


def test_parse_dict_notice():
    bus = EventBus()
    raw = {
        "post_type": "notice",
        "notice_type": "group_increase",
        "user_id": 111,
        "group_id": 222,
    }
    event = bus.parse(raw)
    assert event is not None
    assert event.type == "notice.group_increase"


def test_parse_dict_request():
    bus = EventBus()
    raw = {
        "post_type": "request",
        "request_type": "friend",
        "user_id": 333,
        "comment": "please add me",
    }
    event = bus.parse(raw)
    assert event is not None
    assert event.type == "request.friend"
    assert event.message == "please add me"
    assert event.is_group is False


def test_parse_dict_meta_event():
    bus = EventBus()
    raw = {
        "post_type": "meta_event",
        "meta_event_type": "heartbeat",
    }
    event = bus.parse(raw)
    assert event is not None
    assert event.type == "meta.heartbeat"


def test_parse_unknown_returns_none():
    bus = EventBus()
    assert bus.parse("garbage") is None
    # unknown dict post_type
    raw = {"post_type": "weird_stuff"}
    event = bus.parse(raw)
    assert event is None


# -------------------------------------------------------------------
# dispatch routing
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_routes_message_to_plugin_manager():
    bus = EventBus()
    bot = _DummyBot()
    from src.plugin.base import Event as E

    class _Catcher:
        name = "catcher"
        priority = 0

        def match(self, event):
            return True

        async def handle(self, event, b):
            b._consumed_events.append(event)
            return True

    bot.plugin_manager.register(_Catcher())

    raw = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 1,
        "group_id": 2,
        "raw_message": "/ping",
    }
    await bus.dispatch(raw, bot)
    assert len(bot._consumed_events) == 1
    assert bot._consumed_events[0].type == "message.group"


@pytest.mark.asyncio
async def test_dispatch_skips_unknown_event():
    bus = EventBus()
    bot = _DummyBot()
    await bus.dispatch("garbage", bot)
    assert len(bot._consumed_events) == 0
