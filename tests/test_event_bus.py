"""Tests for EventBus: event parsing and routing."""

import pytest

from src.core.event_bus import EventBus
from src.core.message_bus import MessageBus
from src.plugin.base import Event
from src.plugin.manager import PluginManager


class _DummyBot:
    def __init__(self):
        self.message_bus = MessageBus()
        self.plugin_manager = PluginManager(bus=self.message_bus)
        self._consumed_events: list[Event] = []
        self.api = _DummyApi()

    async def handle(self, event, bot):
        self._consumed_events.append(event)
        return True


class _DummyApi:
    async def _raw_call(self, action, **params):
        return {"status": "ok"}


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
# typed notice events (napcat-sdk)
# -------------------------------------------------------------------


def test_parse_notice_event_typed():
    from napcat import GroupPokeEvent

    bus = EventBus()
    raw = GroupPokeEvent.from_dict({
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "poke",
        "user_id": 789,
        "target_id": 999,
        "group_id": 101112,
        "self_id": 3632757457,
        "time": 1234567890,
        "raw_info": {},
    })
    event = bus.parse(raw)
    assert event is not None
    assert event.type == "notice.notify"
    assert event.user_id == 789
    assert event.group_id == 101112
    assert event.is_group is True
    assert event.raw is raw


# -------------------------------------------------------------------
# dispatch routing
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_routes_message_to_plugin_manager():
    bus = EventBus()
    bot = _DummyBot()

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


# -------------------------------------------------------------------
# listener registration / emission
# -------------------------------------------------------------------


class _ListenerBot:
    """Minimal bot stub for listener tests (no plugin_manager needed)."""

    def __init__(self):
        self.events: list[Event] = []
        self.message_bus = MessageBus()
        self.api = _DummyApi()


@pytest.mark.asyncio
async def test_listener_on_and_emit():
    bus = EventBus()
    bot = _ListenerBot()

    async def _on_notice(event: Event, b: _ListenerBot) -> None:
        b.events.append(event)

    bus.on("notice", _on_notice)

    notice_event = Event(type="notice.group_increase", user_id=111, group_id=222, is_group=True)
    await bus._emit("notice", notice_event, bot)
    assert len(bot.events) == 1
    assert bot.events[0].type == "notice.group_increase"


@pytest.mark.asyncio
async def test_listener_prefix_matching():
    """Only listeners with matching prefix should be called."""
    bus = EventBus()
    bot = _ListenerBot()

    calls: list[str] = []

    async def _on_notice(event: Event, b) -> None:
        calls.append("notice")

    async def _on_request(event: Event, b) -> None:
        calls.append("request")

    bus.on("notice", _on_notice)
    bus.on("request", _on_request)

    await bus._emit("notice", Event(type="notice.group_increase", user_id=1), bot)
    assert calls == ["notice"]


@pytest.mark.asyncio
async def test_listener_sync_wrapped_to_async():
    """Synchronous listeners should be automatically wrapped and work."""
    bus = EventBus()
    bot = _ListenerBot()

    def _sync_handler(event: Event, b) -> None:
        b.events.append(event)

    bus.on("notice", _sync_handler)

    event = Event(type="notice.group_increase", user_id=1)
    await bus._emit("notice", event, bot)
    assert len(bot.events) == 1


@pytest.mark.asyncio
async def test_listener_exception_does_not_block_others():
    """One listener crashing should not prevent others from running."""
    bus = EventBus()
    bot = _ListenerBot()

    async def _bad(event: Event, b) -> None:
        raise RuntimeError("boom")

    async def _good(event: Event, b) -> None:
        b.events.append(event)

    bus.on("notice", _bad)
    bus.on("notice", _good)

    await bus._emit("notice", Event(type="notice.group_increase", user_id=1), bot)
    assert len(bot.events) == 1  # good listener still ran


@pytest.mark.asyncio
async def test_listener_off_removes():
    bus = EventBus()
    bot = _ListenerBot()

    async def _handler(event: Event, b) -> None:
        b.events.append(event)

    bus.on("notice", _handler)
    await bus._emit("notice", Event(type="notice.heartbeat", user_id=1), bot)
    assert len(bot.events) == 1

    bus.off("notice", _handler)
    await bus._emit("notice", Event(type="notice.heartbeat", user_id=1), bot)
    assert len(bot.events) == 1  # no change


def test_listener_off_nonexistent_does_not_crash():
    bus = EventBus()

    def _nop(event: Event, bot) -> None:
        pass

    bus.off("notice", _nop)  # should not raise
