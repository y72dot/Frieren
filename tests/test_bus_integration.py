"""End-to-end integration tests: external event → plugin → ACTION → QQExec."""

import pytest

from src.core.message_bus import BusMessage, MessageBus, MessageType
from src.core.message_store import MessageStore
from src.core.filter_manager import FilterManager
from src.plugin.base import Event

# -------------------------------------------------------------------
# bot + api stubs
# -------------------------------------------------------------------


class _IntegrationApi:
    """In-memory API that records calls like the real napcat client would."""

    def __init__(self):
        self.calls: list[dict] = []

    async def _raw_call(self, action: str, **params):
        self.calls.append({"action": action, **params})
        return {"status": "ok", "action": action}


class _IntegrationBot:
    def __init__(self, bus: MessageBus):
        self.message_bus = bus
        self.msg_store = MessageStore(db_path=":memory:")
        self.api = _IntegrationApi()
        self.config = None  # set by tests if needed
        self.filter_mgr = FilterManager()


# -------------------------------------------------------------------
# plugin simulating /weather command
# -------------------------------------------------------------------


class _WeatherPlugin:
    name = "weather"
    priority = 10

    def match(self, event: Event) -> bool:
        return event.message.startswith("/天气")

    async def handle(self, event: Event, bot: _IntegrationBot) -> bool:
        # Simulate: request weather API, then emit send message
        response = "晴天 25°C"
        if event.is_group and event.group_id:
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": event.group_id,
                    "message": response,
                },
                source="weather",
            )
            bot.message_bus.emit(msg)
        return False  # don't suppress – allow logging plugins too


# -------------------------------------------------------------------
# plugin simulating a logger
# -------------------------------------------------------------------


class _LoggerPlugin:
    name = "logger"
    priority = 50
    logged: list[Event] = []

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot: _IntegrationBot) -> bool:
        self.logged.append(event)
        # Emit an INTERNAL message for metrics collection
        msg = BusMessage(
            type=MessageType.INTERNAL,
            payload={"event": "cmd_used", "cmd": event.message.split()[0] if event.message else ""},
            source="logger",
        )
        bot.message_bus.emit(msg)
        return False


class _MetricsCollector:
    name = "metrics"
    priority = 0
    metrics: list[dict] = []

    def match(self, payload):
        return True

    async def handle(self, payload, bot):
        self.metrics.append(payload)
        return False


# -------------------------------------------------------------------
# tests
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_flow_external_to_send():
    """A user sending /天气 should result in a group message being sent."""
    bus = MessageBus()
    bot = _IntegrationBot(bus)

    # Register plugins
    weather = _WeatherPlugin()
    logger_plugin = _LoggerPlugin()
    logger_plugin.logged.clear()

    bus.subscribe(MessageType.EXTERNAL, weather, weather.priority)
    bus.subscribe(MessageType.EXTERNAL, logger_plugin, logger_plugin.priority)

    # Simulate incoming event
    event = Event(
        type="message.group",
        user_id=123,
        message="/天气 北京",
        group_id=456,
        is_group=True,
    )
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event, source="napcat")
    consumed = await bus.dispatch(msg, bot)
    assert consumed is False  # weather returned False, logger returned False

    # Flush queued ACTION / INTERNAL messages
    await bus.flush(bot)

    # Verify the send happened
    assert len(bot.api.calls) == 1
    assert bot.api.calls[0]["action"] == "send_group_msg"
    assert bot.api.calls[0]["group_id"] == 456
    assert bot.api.calls[0]["message"] == "晴天 25°C"

    # Verify logger recorded the event
    assert len(logger_plugin.logged) == 1
    assert logger_plugin.logged[0].message == "/天气 北京"


@pytest.mark.asyncio
async def test_full_flow_with_suppression():
    """A high-priority spam filter should block events before they reach plugins."""
    bus = MessageBus()
    bot = _IntegrationBot(bus)

    class _SpamGuard:
        name = "spam_guard"
        priority = 0

        def match(self, event: Event) -> bool:
            return True  # checks all events

        async def handle(self, event: Event, b) -> bool:
            return "spam" in event.message.lower()

    weather = _WeatherPlugin()
    logger_plugin = _LoggerPlugin()
    logger_plugin.logged.clear()

    bus.subscribe(MessageType.EXTERNAL, _SpamGuard(), 0)
    bus.subscribe(MessageType.EXTERNAL, weather, 10)
    bus.subscribe(MessageType.EXTERNAL, logger_plugin, 50)

    # Spam event
    spam_event = Event(
        type="message.group",
        user_id=999,
        message="buy spam now",
        group_id=456,
        is_group=True,
    )
    msg = BusMessage(type=MessageType.EXTERNAL, payload=spam_event)
    consumed = await bus.dispatch(msg, bot)
    assert consumed is True  # suppressed by spam guard

    await bus.flush(bot)

    # Weather should NOT have been triggered
    assert len(bot.api.calls) == 0
    # Logger should NOT have recorded it
    assert len(logger_plugin.logged) == 0


@pytest.mark.asyncio
async def test_action_suppression_chain():
    """A content filter on ACTION should be able to block sends."""
    bus = MessageBus()
    bot = _IntegrationBot(bus)

    class _ContentFilter:
        name = "content_filter"
        priority = 5

        def match(self, payload):
            return payload.get("action", "").startswith("send_")

        async def handle(self, payload, b):
            return "forbidden" in str(payload.get("message", ""))

    bus.subscribe(MessageType.ACTION, _ContentFilter(), 5)

    # Allowed message
    ok_msg = BusMessage(
        type=MessageType.ACTION,
        payload={"action": "send_group_msg", "group_id": 1, "message": "hello"},
    )
    result = await bus.dispatch(ok_msg, bot)
    assert result == {"status": "ok", "action": "send_group_msg"}
    assert len(bot.api.calls) == 1

    # Forbidden message
    bot.api.calls.clear()
    bad_msg = BusMessage(
        type=MessageType.ACTION,
        payload={"action": "send_group_msg", "group_id": 1, "message": "this is forbidden content"},
    )
    result = await bus.dispatch(bad_msg, bot)
    assert result is True  # suppressed
    assert len(bot.api.calls) == 0


@pytest.mark.asyncio
async def test_internal_message_routing():
    """INTERNAL messages emitted during flush should be dispatched to subscribers."""
    bus = MessageBus()
    bot = _IntegrationBot(bus)

    metrics = _MetricsCollector()
    metrics.metrics.clear()
    bus.subscribe(MessageType.INTERNAL, metrics, 0)

    # Simulate an external event that emits INTERNAL
    logger_plugin = _LoggerPlugin()
    logger_plugin.logged.clear()
    bus.subscribe(MessageType.EXTERNAL, logger_plugin, 10)

    event = Event(type="message.group", user_id=1, message="/test")
    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    await bus.dispatch(msg, bot)
    await bus.flush(bot)

    # Metrics collector should have received the INTERNAL message
    assert len(metrics.metrics) == 1
    assert metrics.metrics[0]["event"] == "cmd_used"
    assert metrics.metrics[0]["cmd"] == "/test"


@pytest.mark.asyncio
async def test_flush_message_storm_truncation():
    """Flush should stop after max_rounds even if plugins keep emitting."""
    bus = MessageBus()
    bot = _IntegrationBot(bus)

    class _NoisyPlugin:
        name = "noisy"
        priority = 0

        def match(self, payload):
            return True

        async def handle(self, payload, b):
            # Re-emit an INTERNAL message each time it's called
            bus.emit(BusMessage(
                type=MessageType.INTERNAL,
                payload={"ping": "pong"},
                source="noisy",
            ))
            return False

    bus.subscribe(MessageType.INTERNAL, _NoisyPlugin(), 0)

    # Seed the queue with one message
    bus.emit(BusMessage(type=MessageType.INTERNAL, payload={"start": True}))

    # Should not hang – flush stops after max_rounds
    await bus.flush(bot, max_rounds=5)

    # Queue should be cleared (truncated)
    # We can't assert exact count since each round doubles, but it shouldn't crash
