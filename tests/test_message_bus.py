"""Tests for MessageBus: subscribe, dispatch, emit, flush, suppression."""

import pytest

from src.core.message_bus import BusMessage, MessageBus, MessageType
from src.plugin.base import Event

# -------------------------------------------------------------------
# minimal bot stub
# -------------------------------------------------------------------


class _MinimalBot:
    """Bot stub exposing message_bus and api for bus tests."""

    def __init__(self, bus: MessageBus):
        self.message_bus = bus
        self.api = _MinimalApi()


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
    assert bus.subscription_count == 2  # ping + built-in _qq_exec for ACTION


def test_subscribe_duplicate_replaces():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 10)
    assert bus.subscription_count == 2  # ping + _qq_exec


def test_unsubscribe_removes():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    assert bus.unsubscribe(MessageType.EXTERNAL, "ping") is True
    assert bus.subscription_count == 1  # only _qq_exec


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
# dispatch – ACTION (suppressible, built-in _qq_exec)
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_action_qq_exec():
    """ACTION messages should be handled by the built-in _qq_exec."""
    bus = MessageBus()
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
    """A spam filter (pri=0) should suppress spam messages before _qq_exec."""
    bus = MessageBus()
    bus.subscribe(MessageType.ACTION, _SpamFilter(), 0)
    bot = _MinimalBot(bus)

    msg = BusMessage(
        type=MessageType.ACTION,
        payload={"action": "send_group_msg", "group_id": 123, "message": "buy spam now!!!"},
    )
    result = await bus.dispatch(msg, bot)
    assert result is True  # suppressed by spam filter
    assert len(bot.api.calls) == 0  # _qq_exec never called


@pytest.mark.asyncio
async def test_dispatch_action_spam_filter_passes():
    """Non-spam messages should pass through the filter to _qq_exec."""
    bus = MessageBus()
    bus.subscribe(MessageType.ACTION, _SpamFilter(), 0)
    bot = _MinimalBot(bus)

    msg = BusMessage(
        type=MessageType.ACTION,
        payload={"action": "send_group_msg", "group_id": 123, "message": "hello world"},
    )
    result = await bus.dispatch(msg, bot)
    assert result == {"status": "ok", "action": "send_group_msg"}
    assert len(bot.api.calls) == 1


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
    bot = _MinimalBot(bus)

    bus.emit(BusMessage(
        type=MessageType.ACTION,
        payload={"action": "send_group_msg", "group_id": 1, "message": "hi"},
    ))
    assert len(bot.api.calls) == 0  # not yet processed

    await bus.flush(bot)
    assert len(bot.api.calls) == 1
    assert bot.api.calls[0]["action"] == "send_group_msg"


@pytest.mark.asyncio
async def test_emit_and_wait():
    """emit_and_wait should process immediately, not through the queue."""
    bus = MessageBus()
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

    msg = BusMessage(type=MessageType.EXTERNAL, payload=Event(type="x", user_id=1), depth=11)
    result = await bus.dispatch(msg, bot)
    assert result is False


# -------------------------------------------------------------------
# clear
# -------------------------------------------------------------------


def test_clear_resets_subscriptions():
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PingPlugin(), 0)
    assert bus.subscription_count > 1

    bus.clear()
    # After clear, only built-in _qq_exec remains
    assert bus.subscription_count == 1
    assert len(bus._queue) == 0
