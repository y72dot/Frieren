"""LoadedPlugin state machine, health diagnostics, and state transitions.

Every plugin instance is wrapped in a :class:`LoadedPlugin` that tracks
its lifecycle state, records transitions for auditing, and accumulates
health counters for automatic degradation / healing.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from types import ModuleType

    from src.plugin.context import PluginContext
    from src.plugin.definition import PluginDefinition
    from src.plugin.manifest import PluginManifest
    from src.plugin.scope import ResourceScope


# ---------------------------------------------------------------------------
# PluginState
# ---------------------------------------------------------------------------


class PluginState(StrEnum):
    """Lifecycle states for a loaded plugin."""

    DISCOVERED = "discovered"
    VALIDATED = "validated"
    LOADED = "loaded"
    STARTING = "starting"
    ACTIVE = "active"
    FAILED = "failed"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"


# ---------------------------------------------------------------------------
# valid transitions
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[PluginState, set[PluginState]] = {
    PluginState.DISCOVERED: {PluginState.VALIDATED},
    PluginState.VALIDATED:  {PluginState.LOADED, PluginState.FAILED, PluginState.STOPPING},
    PluginState.LOADED:     {PluginState.STARTING, PluginState.FAILED, PluginState.STOPPING},
    PluginState.STARTING:   {PluginState.ACTIVE, PluginState.FAILED, PluginState.STOPPING},
    PluginState.ACTIVE:     {PluginState.DEGRADED, PluginState.STOPPING},
    PluginState.DEGRADED:   {PluginState.ACTIVE, PluginState.STOPPING},
    PluginState.STOPPING:   {PluginState.STOPPED},
    PluginState.FAILED:     {PluginState.STOPPING},
    PluginState.STOPPED:    set(),
}


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when a plugin attempts an illegal state transition."""

    def __init__(self, plugin_id: str, current: PluginState, target: PluginState) -> None:
        self.plugin_id = plugin_id
        self.current = current
        self.target = target
        super().__init__(
            f"Plugin '{plugin_id}': illegal transition {current.value} → {target.value}"
        )


# ---------------------------------------------------------------------------
# StateTransition (audit record)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateTransition:
    """Immutable record of a single state change."""

    plugin_id: str
    version: str
    generation: int
    from_state: PluginState | None
    to_state: PluginState
    reason: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# PluginHealth
# ---------------------------------------------------------------------------


@dataclass
class PluginHealth:
    """Runtime health counters for a single plugin."""

    match_count: int = 0
    handle_count: int = 0
    consume_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0
    last_success_at: float | None = None
    last_error_at: float | None = None
    last_error_message: str = ""
    timeout_count: int = 0
    permission_denied_count: int = 0
    total_match_ms: float = 0.0
    total_handle_ms: float = 0.0

    def record_success(self, elapsed_ms: float = 0.0) -> None:
        """Record a successful match + handle cycle."""
        self.match_count += 1
        self.handle_count += 1
        self.consecutive_errors = 0
        self.last_success_at = time.time()
        self.total_match_ms += elapsed_ms
        self.total_handle_ms += elapsed_ms

    def record_error(self, message: str) -> None:
        """Record a handler error."""
        self.error_count += 1
        self.consecutive_errors += 1
        self.last_error_at = time.time()
        self.last_error_message = message


# ---------------------------------------------------------------------------
# LoadedPlugin
# ---------------------------------------------------------------------------


@dataclass
class LoadedPlugin:
    """A plugin instance tracked through its lifecycle state machine.

    Wraps manifest + definition with runtime state, health counters,
    a generation-scoped resource bundle, and an audit trail of state
    transitions.
    """

    manifest: PluginManifest
    definition: PluginDefinition
    module: ModuleType | None = None
    instance: Any = None
    generation: int = 0
    state: PluginState = PluginState.DISCOVERED
    scope: ResourceScope | None = None
    context: PluginContext | None = None
    health: PluginHealth = field(default_factory=PluginHealth)
    loaded_at: float = 0.0
    _transitions: list[StateTransition] = field(default_factory=list)
    _error: str = ""

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def plugin_id(self) -> str:
        """Shortcut to ``manifest.id``."""
        return self.manifest.id

    @property
    def version(self) -> str:
        """Shortcut to ``manifest.version``."""
        return self.manifest.version

    # ------------------------------------------------------------------
    # state machine
    # ------------------------------------------------------------------

    def transition(self, target: PluginState, reason: str) -> LoadedPlugin:
        """Attempt a state transition; raises :class:`InvalidTransitionError` on failure.

        Returns *self* for chaining.
        """
        if target not in _VALID_TRANSITIONS.get(self.state, set()):
            raise InvalidTransitionError(self.plugin_id, self.state, target)

        t = StateTransition(
            plugin_id=self.plugin_id,
            version=self.version,
            generation=self.generation,
            from_state=self.state,
            to_state=target,
            reason=reason,
        )
        self._transitions.append(t)
        self.state = target
        logger.info(
            f"Plugin '{self.plugin_id}' gen={self.generation}: "
            f"{t.from_state.value if t.from_state else 'nil'} → {t.to_state.value}"
            f" (reason: {reason})"
        )
        return self

    def set_failed(self, error: str) -> LoadedPlugin:
        """Shortcut: transition to FAILED and store the error message."""
        self._error = error
        try:
            self.transition(PluginState.FAILED, error)
        except InvalidTransitionError:
            logger.warning(
                f"Plugin '{self.plugin_id}': set_failed() called but "
                f"transition {self.state.value} → FAILED is invalid"
            )
        return self

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------

    def status_summary(self) -> dict:
        """Return all diagnostic fields as a flat dict."""
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "state": self.state.value,
            "generation": self.generation,
            "loaded_at": self.loaded_at,
            "error": self._error,
            "match_count": self.health.match_count,
            "handle_count": self.health.handle_count,
            "consume_count": self.health.consume_count,
            "error_count": self.health.error_count,
            "consecutive_errors": self.health.consecutive_errors,
            "last_success_at": self.health.last_success_at,
            "last_error_at": self.health.last_error_at,
            "last_error_message": self.health.last_error_message,
            "timeout_count": self.health.timeout_count,
            "permission_denied_count": self.health.permission_denied_count,
            "total_match_ms": self.health.total_match_ms,
            "total_handle_ms": self.health.total_handle_ms,
            "transition_count": len(self._transitions),
        }
