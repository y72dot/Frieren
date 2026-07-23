"""P0 PLUG-001: Baseline snapshot of all current plugins and their behavior.

This test records the current plugin inventory, event type subscriptions,
priorities, and consumption behavior.  Any change to this snapshot must be
intentional and documented in the refactoring plan.
"""

from __future__ import annotations

import pytest

from src.core.filter_manager import FilterManager
from src.core.message_bus import MessageBus, MessageType
from src.plugin.manager import PluginManager

# ---------------------------------------------------------------------------
# Helper: capture current plugin inventory
# ---------------------------------------------------------------------------


def _capture_plugin_snapshot() -> dict:
    """Discover all plugins and return a structured snapshot."""
    pm = PluginManager(bus=MessageBus())
    pm.auto_discover(plugin_dirs=["plugins"], disabled=[])

    registry: dict[str, dict] = {}

    for plugin in pm.plugins:
        entry = {
            "name": plugin.name,
            "priority": plugin.priority,
        }
        registry[plugin.name] = entry

    # Capture bus subscriptions by message type
    bus_snapshot: dict[str, list[dict]] = {}
    for mtype in MessageType:
        subs = pm._bus._subscriptions[mtype]
        bus_snapshot[mtype.value] = sorted(
            [
                {"handler": s.handler.name, "priority": s.priority}
                for s in subs
            ],
            key=lambda x: x["priority"],
        )

    return {
        "plugin_count": pm.plugin_count,
        "plugins": dict(sorted(registry.items())),
        "subscriptions_by_type": bus_snapshot,
    }


# ---------------------------------------------------------------------------
# Test: snapshot matches expected values
# ---------------------------------------------------------------------------


def test_plugin_baseline_snapshot():
    """Assert the current plugin inventory matches the expected baseline.

    If this test fails because you intentionally added, removed, or changed
    a plugin, update the expected values below AND document the change in
    PLUGIN_SYSTEM_REFACTOR_PLAN.md.
    """
    snapshot = _capture_plugin_snapshot()

    # -- plugin count --
    # 10 package plugins, 11 handler adapters registered
    # (llm_gate registers 2 event handlers)
    assert snapshot["plugin_count"] == 11, (
        f"Plugin count changed: {snapshot['plugin_count']} != 11"
    )

    # -- plugin names and priorities (sorted alphabetically) --
    # Adapter name format:
    #   commands: "{command_name}" (e.g. "/ping")
    #   events: "{event_type}:{priority}"
    #   internal: "int:{topic}"
    #   observers: "obs:{event_type}"
    expected_plugins = {
        "/echo": 0,
        "/ping": 0,
        "int:send": 0,
        "int:trigger": 0,
        "message.group:100": 100,  # repeater
        "message.group:5": 5,      # llm_gate (group)
        "message.group:50": 50,    # sticker_react
        "message.group:51": 51,    # essence
        "message.private:5": 5,    # llm_gate (private)
        "notice.notify:0": 0,      # poke
        "obs:*": 100,              # history
    }

    actual_plugins = {
        name: info["priority"] for name, info in snapshot["plugins"].items()
    }
    assert actual_plugins == expected_plugins, (
        f"Plugin inventory mismatch.\n"
        f"Expected: {expected_plugins}\n"
        f"Got:      {actual_plugins}"
    )

    # -- subscriptions by type --
    external_expected = [
        "/echo",              # p=0
        "/ping",              # p=0
        "notice.notify:0",    # p=0
        "message.group:5",    # p=5
        "message.private:5",  # p=5
        "message.group:50",   # sticker_react, p=50
        "message.group:51",   # essence, p=51
        "obs:*",              # history, p=100
        "message.group:100",  # repeater, p=100
    ]
    external_subs = snapshot["subscriptions_by_type"]["external"]
    external_names = [s["handler"] for s in external_subs]
    assert external_names == external_expected, (
        f"EXTERNAL mismatch.\nExpected: {external_expected}\nGot:      {external_names}"
    )

    # ACTION: only _qq_exec (p=100) – action_queue uses middleware now
    action_subs = snapshot["subscriptions_by_type"]["action"]
    action_names = [s["handler"] for s in action_subs]
    assert action_names == ["_qq_exec"], (
        f"ACTION mismatch: {action_names}"
    )

    # INTERNAL: int:trigger (p=0), int:send (p=0)
    internal_subs = snapshot["subscriptions_by_type"]["internal"]
    internal_names = [s["handler"] for s in internal_subs]
    assert internal_names == ["int:trigger", "int:send"], (
        f"INTERNAL mismatch: {internal_names}"
    )

    # LIFECYCLE: none
    lifecycle_subs = snapshot["subscriptions_by_type"]["lifecycle"]
    assert len(lifecycle_subs) == 0


# ---------------------------------------------------------------------------
# Test: consumption behavior
# ---------------------------------------------------------------------------


class _ConsumePlugin:
    name = "consumer"
    priority = 0

    def match(self, event):
        return True

    async def handle(self, event, bot):
        return True


class _PassthroughPlugin:
    name = "passthrough"
    priority = 5

    def match(self, event):
        return True

    async def handle(self, event, bot):
        return False


@pytest.mark.asyncio
async def test_external_first_consumer_wins():
    """EXTERNAL: first plugin returning truthy consumes the event."""
    bus = MessageBus()
    bus.subscribe(MessageType.EXTERNAL, _PassthroughPlugin(), 5)
    bus.subscribe(MessageType.EXTERNAL, _ConsumePlugin(), 10)

    from src.plugin.base import Event

    event = Event(type="message.group", user_id=1, message="test")

    # Minimal bot for dispatch
    class _Bot:
        def __init__(self, bus):
            self.message_bus = bus
            self.filter_mgr = FilterManager()

    bot = _Bot(bus)
    from src.core.message_bus import BusMessage

    msg = BusMessage(type=MessageType.EXTERNAL, payload=event)
    result = await bus.dispatch(msg, bot)
    # Passthrough (p=5) runs first, returns False, so Consumer (p=10) runs
    # and returns True → consumed
    assert result is True


@pytest.mark.asyncio
async def test_internal_all_handlers_run():
    """INTERNAL: all handlers execute regardless of return values."""
    bus = MessageBus()

    received_a = []
    received_b = []

    class _HandlerA:
        name = "handler_a"
        priority = 0

        def match(self, payload):
            return True

        async def handle(self, payload, bot):
            received_a.append(payload)
            return True

    class _HandlerB:
        name = "handler_b"
        priority = 10

        def match(self, payload):
            return True

        async def handle(self, payload, bot):
            received_b.append(payload)
            return False

    bus.subscribe(MessageType.INTERNAL, _HandlerA(), 0)
    bus.subscribe(MessageType.INTERNAL, _HandlerB(), 10)

    from src.core.message_bus import BusMessage

    class _Bot:
        def __init__(self, bus):
            self.message_bus = bus
            self.filter_mgr = FilterManager()

    msg = BusMessage(type=MessageType.INTERNAL, payload={"key": "val"})
    result = await bus.dispatch(msg, _Bot(bus))
    assert result is None  # INTERNAL always returns None
    assert len(received_a) == 1
    assert len(received_b) == 1


@pytest.mark.asyncio
async def test_action_qq_exec_is_last():
    """ACTION: _qq_exec is always at priority 100 (last after interceptors)."""
    bus = MessageBus()

    # Verify _qq_exec is registered at priority 100
    qq_subs = [
        s
        for s in bus._subscriptions[MessageType.ACTION]
        if s.handler.name == "_qq_exec"
    ]
    assert len(qq_subs) == 1
    assert qq_subs[0].priority == 100
    assert qq_subs[0].handler.name == "_qq_exec"
