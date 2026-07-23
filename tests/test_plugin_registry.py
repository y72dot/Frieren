"""Tests for RegistrySnapshot, build_snapshot(), Registry atomic swap, and typed registries."""

from dataclasses import FrozenInstanceError

import pytest

from src.plugin.definition import (
    CommandSpec,
    EventHandlerSpec,
    EventResult,
    InternalHandlerSpec,
    LifecycleHookSpec,
    ObserverSpec,
    PluginDefinition,
)
from src.plugin.loaded import LoadedPlugin, PluginState
from src.plugin.manifest import PluginManifest
from src.plugin.registry import (
    EventRegistry,
    ObserverRegistry,
    Registry,
    RegistrySnapshot,
    build_snapshot,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_manifest(plugin_id: str = "test") -> PluginManifest:
    return PluginManifest(id=plugin_id, version="1.0.0", entrypoint="", sdk="*")


async def _noop_handler(*args, **kwargs):
    pass


def _make_plugin(
    plugin_id: str = "test",
    commands: tuple = (),
    event_handlers: tuple = (),
    observers: tuple = (),
    internal_handlers: tuple = (),
    lifecycle_hooks: tuple = (),
    state: PluginState = PluginState.ACTIVE,
) -> LoadedPlugin:
    manifest = _make_manifest(plugin_id)
    definition = PluginDefinition(
        plugin_id=plugin_id,
        version="1.0.0",
        commands=commands,
        event_handlers=event_handlers,
        observers=observers,
        internal_handlers=internal_handlers,
        lifecycle_hooks=lifecycle_hooks,
    )
    p = LoadedPlugin(manifest=manifest, definition=definition)
    # Force state (skip transition validation for test convenience).
    p.state = state
    return p


# ---------------------------------------------------------------------------
# build_snapshot
# ---------------------------------------------------------------------------


class TestBuildSnapshot:
    def test_active_and_degraded_appear(self):
        p1 = _make_plugin("p1", state=PluginState.ACTIVE)
        p2 = _make_plugin("p2", state=PluginState.DEGRADED)
        p3 = _make_plugin("p3", state=PluginState.FAILED)
        p4 = _make_plugin("p4", state=PluginState.LOADED)

        plugins = {"p1": p1, "p2": p2, "p3": p3, "p4": p4}
        snap = build_snapshot(plugins, 1)
        assert snap.plugin_ids == frozenset({"p1", "p2"})
        assert snap.plugin_count == 2

    def test_empty_no_plugins(self):
        snap = build_snapshot({}, 0)
        assert snap.plugin_count == 0
        assert snap.plugin_ids == frozenset()
        assert snap.generation == 0

    def test_commands_by_name(self):
        cmd = CommandSpec(name="hello", handler=_noop_handler)
        p = _make_plugin("p1", commands=(cmd,))
        snap = build_snapshot({"p1": p}, 1)
        assert "hello" in snap.commands_by_name
        spec, pid = snap.commands_by_name["hello"]
        assert spec.name == "hello"
        assert pid == "p1"

    def test_command_conflict_first_wins(self):
        cmd_a = CommandSpec(name="hello", handler=_noop_handler)
        cmd_b = CommandSpec(name="hello", handler=_noop_handler)
        p1 = _make_plugin("p1", commands=(cmd_a,))
        p2 = _make_plugin("p2", commands=(cmd_b,))
        # p1 is first in dict order
        snap = build_snapshot({"p1": p1, "p2": p2}, 1)
        spec, pid = snap.commands_by_name["hello"]
        assert pid == "p1"

    def test_consumers_sorted_by_priority(self):
        eh1 = EventHandlerSpec(event_type="message.group", priority=50, handler=_noop_handler)
        eh2 = EventHandlerSpec(event_type="message.group", priority=10, handler=_noop_handler)
        p = _make_plugin("p1", event_handlers=(eh1, eh2))
        snap = build_snapshot({"p1": p}, 1)
        consumers = snap.consumers_by_event_type["message.group"]
        assert consumers[0][0].priority <= consumers[1][0].priority

    def test_observers_indexed(self):
        obs = ObserverSpec(event_type="notice.notify", handler=_noop_handler)
        p = _make_plugin("p1", observers=(obs,))
        snap = build_snapshot({"p1": p}, 1)
        assert "notice.notify" in snap.observers_by_event_type

    def test_internal_handlers_by_topic(self):
        ih = InternalHandlerSpec(message_type="internal", handler=_noop_handler, topic="test.topic")
        p = _make_plugin("p1", internal_handlers=(ih,))
        snap = build_snapshot({"p1": p}, 1)
        assert "test.topic" in snap.internal_handlers_by_topic

    def test_internal_handler_empty_topic_as_all(self):
        ih = InternalHandlerSpec(message_type="internal", handler=_noop_handler, topic="")
        p = _make_plugin("p1", internal_handlers=(ih,))
        snap = build_snapshot({"p1": p}, 1)
        assert "" in snap.internal_handlers_by_topic

    def test_lifecycle_handlers(self):
        lh = LifecycleHookSpec(hook_type="setup", handler=_noop_handler)
        p = _make_plugin("p1", lifecycle_hooks=(lh,))
        snap = build_snapshot({"p1": p}, 1)
        assert len(snap.lifecycle_handlers) == 1

    def test_snapshot_immutable(self):
        snap = build_snapshot({}, 0)
        with pytest.raises(FrozenInstanceError):
            snap.plugin_count = 99


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_publish_atomic_swap(self):
        r = Registry()
        old = r.current
        snap = RegistrySnapshot(generation=1, plugin_count=5)
        returned_old = r.publish(snap)
        assert returned_old is old
        assert r.current is snap

    def test_rollback_restores_previous(self):
        r = Registry()
        snap1 = RegistrySnapshot(generation=1, plugin_count=3)
        r.publish(snap1)
        restored = r.rollback()
        assert restored is not None
        assert restored.plugin_count == 0  # original empty
        assert r.current.plugin_count == 0

    def test_rollback_no_previous_returns_none(self):
        r = Registry()
        result = r.rollback()
        assert result is None


# ---------------------------------------------------------------------------
# EventResult
# ---------------------------------------------------------------------------


class TestEventResult:
    def test_consume_to_bool(self):
        assert EventResult.CONSUME.to_bool() is True

    def test_continue_to_bool(self):
        assert EventResult.CONTINUE.to_bool() is False

    def test_from_bool_true(self):
        assert EventResult.from_bool(True) == EventResult.CONSUME

    def test_from_bool_false(self):
        assert EventResult.from_bool(False) == EventResult.CONTINUE

    def test_round_trip(self):
        for value in (True, False):
            assert EventResult.from_bool(value).to_bool() == value


# ---------------------------------------------------------------------------
# EventRegistry
# ---------------------------------------------------------------------------


class TestEventRegistry:
    def test_get_consumers_returns_priority_order(self):
        eh1 = EventHandlerSpec(event_type="message.group", priority=50, handler=_noop_handler)
        eh2 = EventHandlerSpec(event_type="message.group", priority=10, handler=_noop_handler)
        p = _make_plugin("p1", event_handlers=(eh1, eh2))
        snap = build_snapshot({"p1": p}, 1)
        reg = EventRegistry(snap)
        consumers = reg.get_consumers("message.group")
        assert len(consumers) == 2
        assert consumers[0][0].priority <= consumers[1][0].priority

    def test_get_consumers_includes_wildcard(self):
        eh1 = EventHandlerSpec(event_type="message.group", priority=10, handler=_noop_handler)
        eh2 = EventHandlerSpec(event_type="*", priority=5, handler=_noop_handler)
        p = _make_plugin("p1", event_handlers=(eh1, eh2))
        snap = build_snapshot({"p1": p}, 1)
        reg = EventRegistry(snap)
        consumers = reg.get_consumers("message.group")
        # Both specific and wildcard handlers
        assert len(consumers) == 2
        priorities = [c[0].priority for c in consumers]
        assert priorities == sorted(priorities)

    def test_get_consumers_unknown_event_type(self):
        p = _make_plugin("p1")
        snap = build_snapshot({"p1": p}, 1)
        reg = EventRegistry(snap)
        consumers = reg.get_consumers("nonexistent")
        assert consumers == []

    def test_consumer_count_total(self):
        eh1 = EventHandlerSpec(event_type="message.group", priority=10, handler=_noop_handler)
        eh2 = EventHandlerSpec(event_type="message.private", priority=10, handler=_noop_handler)
        p = _make_plugin("p1", event_handlers=(eh1, eh2))
        snap = build_snapshot({"p1": p}, 1)
        reg = EventRegistry(snap)
        assert reg.consumer_count() == 2

    def test_consumer_count_specific(self):
        eh1 = EventHandlerSpec(event_type="message.group", priority=10, handler=_noop_handler)
        p = _make_plugin("p1", event_handlers=(eh1,))
        snap = build_snapshot({"p1": p}, 1)
        reg = EventRegistry(snap)
        assert reg.consumer_count("message.group") == 1
        assert reg.consumer_count("message.private") == 0

    def test_event_types(self):
        eh1 = EventHandlerSpec(event_type="message.group", priority=10, handler=_noop_handler)
        eh2 = EventHandlerSpec(event_type="*", priority=5, handler=_noop_handler)
        p = _make_plugin("p1", event_handlers=(eh1, eh2))
        snap = build_snapshot({"p1": p}, 1)
        reg = EventRegistry(snap)
        types = reg.event_types()
        assert "message.group" in types
        assert "*" in types


# ---------------------------------------------------------------------------
# ObserverRegistry
# ---------------------------------------------------------------------------


class TestObserverRegistry:
    def test_get_observers_returns_both_specific_and_wildcard(self):
        obs1 = ObserverSpec(event_type="notice.notify", handler=_noop_handler)
        obs2 = ObserverSpec(event_type="*", handler=_noop_handler)
        p = _make_plugin("p1", observers=(obs1, obs2))
        snap = build_snapshot({"p1": p}, 1)
        reg = ObserverRegistry(snap)
        observers = reg.get_observers("notice.notify")
        assert len(observers) == 2

    def test_get_observers_only_wildcard(self):
        obs = ObserverSpec(event_type="*", handler=_noop_handler)
        p = _make_plugin("p1", observers=(obs,))
        snap = build_snapshot({"p1": p}, 1)
        reg = ObserverRegistry(snap)
        observers = reg.get_observers("message.group")
        assert len(observers) == 1

    def test_observers_separate_from_consumers(self):
        eh = EventHandlerSpec(event_type="message.group", priority=10, handler=_noop_handler)
        obs = ObserverSpec(event_type="message.group", handler=_noop_handler)
        p = _make_plugin("p1", event_handlers=(eh,), observers=(obs,))
        snap = build_snapshot({"p1": p}, 1)
        ev_reg = EventRegistry(snap)
        obs_reg = ObserverRegistry(snap)
        assert ev_reg.consumer_count() == 1
        assert obs_reg.observer_count() == 1

    def test_observer_count(self):
        obs1 = ObserverSpec(event_type="notice.notify", handler=_noop_handler)
        obs2 = ObserverSpec(event_type="message.group", handler=_noop_handler)
        p = _make_plugin("p1", observers=(obs1, obs2))
        snap = build_snapshot({"p1": p}, 1)
        reg = ObserverRegistry(snap)
        assert reg.observer_count() == 2

    def test_event_types(self):
        obs = ObserverSpec(event_type="notice.notify", handler=_noop_handler)
        p = _make_plugin("p1", observers=(obs,))
        snap = build_snapshot({"p1": p}, 1)
        reg = ObserverRegistry(snap)
        assert "notice.notify" in reg.event_types()
