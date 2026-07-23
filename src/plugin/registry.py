"""RegistrySnapshot & Registry – atomic handler index for hot-reload.

Every generation of loaded plugins produces an immutable
:class:`RegistrySnapshot` that indexes all handlers by message type,
command name, etc.  The :class:`Registry` holds the *current* snapshot
and supports atomic publish / rollback so hot-reload never leaves the
bot in a half-upgraded state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from src.plugin.loaded import PluginState

if TYPE_CHECKING:
    from src.plugin.definition import (
        CommandSpec,
        EventHandlerSpec,
        InternalHandlerSpec,
        LifecycleHookSpec,
        ObserverSpec,
    )
    from src.plugin.loaded import LoadedPlugin


# ---------------------------------------------------------------------------
# RegistrySnapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistrySnapshot:
    """Immutable, queryable index of all active plugin handlers.

    Built by :func:`build_snapshot` after a generation of plugins has
    been activated.  Only plugins in ``ACTIVE`` or ``DEGRADED`` state
    are included.
    """

    generation: int
    commands_by_name: dict[str, tuple[CommandSpec, str]] = field(default_factory=dict)
    consumers_by_event_type: dict[str, list[tuple[EventHandlerSpec, str]]] = field(
        default_factory=dict
    )
    observers_by_event_type: dict[str, list[tuple[ObserverSpec, str]]] = field(
        default_factory=dict
    )
    internal_handlers_by_topic: dict[str, list[tuple[InternalHandlerSpec, str]]] = field(
        default_factory=dict
    )
    lifecycle_handlers: tuple[tuple[LifecycleHookSpec, str], ...] = ()
    plugin_ids: frozenset[str] = field(default_factory=frozenset)
    plugin_count: int = 0


# ---------------------------------------------------------------------------
# build_snapshot
# ---------------------------------------------------------------------------


def build_snapshot(
    plugins: dict[str, LoadedPlugin], generation: int
) -> RegistrySnapshot:
    """Build a frozen handler index from all active & degraded plugins.

    Parameters
    ----------
    plugins:
        Mapping of ``plugin_id`` → :class:`LoadedPlugin`.
    generation:
        Monotonic generation counter for this snapshot.
    """
    commands_by_name: dict[str, tuple[CommandSpec, str]] = {}
    consumers_by_event_type: dict[str, list[tuple[EventHandlerSpec, str]]] = {}
    observers_by_event_type: dict[str, list[tuple[ObserverSpec, str]]] = {}
    internal_handlers_by_topic: dict[str, list[tuple[InternalHandlerSpec, str]]] = {}
    lifecycle_handlers: list[tuple[LifecycleHookSpec, str]] = []
    active_ids: set[str] = set()

    for plugin_id, p in plugins.items():
        if p.state not in (PluginState.ACTIVE, PluginState.DEGRADED):
            continue

        active_ids.add(plugin_id)
        d = p.definition

        # Commands.
        for cmd in d.commands:
            if cmd.name in commands_by_name:
                existing_spec, existing_pid = commands_by_name[cmd.name]
                logger.error(
                    f"Command name conflict: '{cmd.name}' declared by "
                    f"'{plugin_id}' and '{existing_pid}'. "
                    f"First-registered ('{existing_pid}') wins."
                )
                continue
            commands_by_name[cmd.name] = (cmd, plugin_id)

        # Event consumers.
        for eh in d.event_handlers:
            et = eh.event_type or "*"
            consumers_by_event_type.setdefault(et, []).append((eh, plugin_id))

        # Observers.
        for obs in d.observers:
            et = obs.event_type or "*"
            observers_by_event_type.setdefault(et, []).append((obs, plugin_id))

        # Internal handlers.
        for ih in d.internal_handlers:
            topic = ih.topic or ""
            internal_handlers_by_topic.setdefault(topic, []).append((ih, plugin_id))

        # Lifecycle hooks.
        for lh in d.lifecycle_hooks:
            lifecycle_handlers.append((lh, plugin_id))

    # Sort consumers by priority ascending.
    for et in consumers_by_event_type:
        consumers_by_event_type[et].sort(key=lambda x: x[0].priority)

    return RegistrySnapshot(
        generation=generation,
        commands_by_name=commands_by_name,
        consumers_by_event_type=consumers_by_event_type,
        observers_by_event_type=observers_by_event_type,
        internal_handlers_by_topic=internal_handlers_by_topic,
        lifecycle_handlers=tuple(lifecycle_handlers),
        plugin_ids=frozenset(active_ids),
        plugin_count=len(active_ids),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EventRegistry – typed dispatch view for event consumers
# ---------------------------------------------------------------------------


class EventRegistry:
    """Typed dispatch view for event consumers (CONSUME/CONTINUE contract)."""

    def __init__(self, snapshot: RegistrySnapshot) -> None:
        self._consumers = snapshot.consumers_by_event_type

    def get_consumers(self, event_type: str) -> list[tuple[EventHandlerSpec, str]]:
        """Consumers for *event_type* plus '*' wildcard, sorted by priority."""
        result: list[tuple[EventHandlerSpec, str]] = []
        result.extend(self._consumers.get(event_type, []))
        result.extend(self._consumers.get("*", []))
        result.sort(key=lambda x: x[0].priority)
        return result

    def consumer_count(self, event_type: str = "") -> int:
        """Total consumers across all event types (or for a specific type)."""
        if event_type:
            return len(self.get_consumers(event_type))
        return sum(len(v) for v in self._consumers.values())

    def event_types(self) -> frozenset[str]:
        """All event types with registered consumers."""
        return frozenset(self._consumers.keys())


# ---------------------------------------------------------------------------
# ObserverRegistry – typed dispatch view for observers
# ---------------------------------------------------------------------------


class ObserverRegistry:
    """Typed dispatch view for observers (always broadcast, never consume)."""

    def __init__(self, snapshot: RegistrySnapshot) -> None:
        self._observers = snapshot.observers_by_event_type

    def get_observers(self, event_type: str) -> list[tuple[ObserverSpec, str]]:
        """Observers for *event_type* plus '*' wildcard."""
        result: list[tuple[ObserverSpec, str]] = []
        result.extend(self._observers.get(event_type, []))
        result.extend(self._observers.get("*", []))
        return result

    def observer_count(self, event_type: str = "") -> int:
        """Total observers across all event types (or for a specific type)."""
        if event_type:
            return len(self.get_observers(event_type))
        return sum(len(v) for v in self._observers.values())

    def event_types(self) -> frozenset[str]:
        """All event types with registered observers."""
        return frozenset(self._observers.keys())


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class Registry:
    """Atomic snapshot holder.

    The *current* snapshot is always the one the bus should be using.
    ``publish()`` atomically swaps it; ``rollback()`` restores the
    previous one.
    """

    current: RegistrySnapshot = field(
        default_factory=lambda: RegistrySnapshot(generation=0)
    )
    _previous: RegistrySnapshot | None = None

    def publish(self, snapshot: RegistrySnapshot) -> RegistrySnapshot:
        """Atomically set *snapshot* as the current registry.

        Returns the **old** snapshot (so the caller can drain stale
        plugin generations).
        """
        old = self.current
        self._previous = old
        self.current = snapshot
        logger.info(
            f"Registry published gen={snapshot.generation}: "
            f"{snapshot.plugin_count} plugin(s) active"
        )
        return old

    def rollback(self) -> RegistrySnapshot | None:
        """Restore the previous snapshot.

        Returns the restored snapshot, or ``None`` if there was no
        previous snapshot to roll back to.
        """
        if self._previous is None:
            return None
        restored = self._previous
        self.current = restored
        self._previous = None
        logger.info(f"Registry rolled back to gen={restored.generation}")
        return restored
