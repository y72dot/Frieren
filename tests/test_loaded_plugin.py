"""Tests for PluginState machine, LoadedPlugin, PluginHealth, and transitions."""


import pytest

from src.plugin.definition import PluginDefinition
from src.plugin.loaded import (
    InvalidTransitionError,
    LoadedPlugin,
    PluginHealth,
    PluginState,
)
from src.plugin.manifest import PluginManifest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_manifest(plugin_id: str = "test_plugin", version: str = "1.0.0") -> PluginManifest:
    return PluginManifest(
        id=plugin_id,
        version=version,
        entrypoint="",
        sdk="*",
    )


def _make_definition(plugin_id: str = "test_plugin", version: str = "1.0.0") -> PluginDefinition:
    return PluginDefinition(plugin_id=plugin_id, version=version)


def _make_plugin(plugin_id: str = "test_plugin") -> LoadedPlugin:
    return LoadedPlugin(
        manifest=_make_manifest(plugin_id),
        definition=_make_definition(plugin_id),
    )


# ---------------------------------------------------------------------------
# valid transition walkthrough
# ---------------------------------------------------------------------------


class TestValidTransitions:
    """Happy-path walkthrough of the entire state machine."""

    def test_full_walkthrough(self):
        p = _make_plugin()
        assert p.state == PluginState.DISCOVERED

        p.transition(PluginState.VALIDATED, "manifest ok")
        assert p.state == PluginState.VALIDATED

        p.transition(PluginState.LOADED, "imported")
        assert p.state == PluginState.LOADED

        p.transition(PluginState.STARTING, "begin start hooks")
        assert p.state == PluginState.STARTING

        p.transition(PluginState.ACTIVE, "start hooks finished")
        assert p.state == PluginState.ACTIVE

        p.transition(PluginState.STOPPING, "shutdown")
        assert p.state == PluginState.STOPPING

        p.transition(PluginState.STOPPED, "cleanup done")
        assert p.state == PluginState.STOPPED

    def test_validated_to_failed(self):
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.FAILED, "import error")
        assert p.state == PluginState.FAILED

    def test_failed_to_stopping(self):
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.FAILED, "import error")
        p.transition(PluginState.STOPPING, "cleanup after failure")
        assert p.state == PluginState.STOPPING
        p.transition(PluginState.STOPPED, "done")
        assert p.state == PluginState.STOPPED

    def test_active_to_degraded_and_back(self):
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")
        p.transition(PluginState.STARTING, "starting")
        p.transition(PluginState.ACTIVE, "running")
        p.transition(PluginState.DEGRADED, "too many errors")
        assert p.state == PluginState.DEGRADED
        p.transition(PluginState.ACTIVE, "healed")
        assert p.state == PluginState.ACTIVE

    def test_loaded_direct_to_stopping(self):
        """LOADED → STOPPING valid (skip start phase)."""
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")
        p.transition(PluginState.STOPPING, "shutdown before start")
        assert p.state == PluginState.STOPPING

    def test_validated_to_stopping(self):
        """VALIDATED → STOPPING valid (cleanup at discovery stage)."""
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.STOPPING, "cancelled before load")
        assert p.state == PluginState.STOPPING


# ---------------------------------------------------------------------------
# invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    """Every illegal jump must raise InvalidTransitionError."""

    def test_discovered_to_active(self):
        p = _make_plugin()
        with pytest.raises(InvalidTransitionError):
            p.transition(PluginState.ACTIVE, "jump")

    def test_active_to_failed(self):
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")
        p.transition(PluginState.STARTING, "starting")
        p.transition(PluginState.ACTIVE, "running")
        with pytest.raises(InvalidTransitionError):
            p.transition(PluginState.FAILED, "no direct fail")

    def test_failed_to_active(self):
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.FAILED, "error")
        with pytest.raises(InvalidTransitionError):
            p.transition(PluginState.ACTIVE, "can't revive directly")

    def test_duplicate_same_state(self):
        """Transition to the same state raises InvalidTransitionError."""
        p = _make_plugin()
        with pytest.raises(InvalidTransitionError):
            p.transition(PluginState.DISCOVERED, "same state")


# ---------------------------------------------------------------------------
# transition records
# ---------------------------------------------------------------------------


class TestTransitionRecords:
    def test_trace_id_and_timestamp(self):
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        assert len(p._transitions) == 1
        t = p._transitions[0]
        assert isinstance(t.trace_id, str)
        assert len(t.trace_id) == 8
        assert t.timestamp > 0

    def test_transition_fields(self):
        p = _make_plugin("myplug")
        p.generation = 3
        p.transition(PluginState.VALIDATED, "validated ok")
        t = p._transitions[0]
        assert t.plugin_id == "myplug"
        assert t.version == "1.0.0"
        assert t.generation == 3
        assert t.from_state == PluginState.DISCOVERED
        assert t.to_state == PluginState.VALIDATED
        assert t.reason == "validated ok"


# ---------------------------------------------------------------------------
# health counters
# ---------------------------------------------------------------------------


class TestHealth:
    def test_record_success_resets_consecutive(self):
        h = PluginHealth()
        h.consecutive_errors = 5
        h.record_success(elapsed_ms=10.0)
        assert h.consecutive_errors == 0
        assert h.match_count == 1
        assert h.handle_count == 1
        assert h.last_success_at is not None

    def test_record_error_increments(self):
        h = PluginHealth()
        h.record_error("boom")
        assert h.error_count == 1
        assert h.consecutive_errors == 1
        assert h.last_error_at is not None
        assert h.last_error_message == "boom"
        h.record_error("boom again")
        assert h.error_count == 2
        assert h.consecutive_errors == 2

    def test_set_failed_stores_error(self):
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.set_failed("import error: foo")
        assert p.state == PluginState.FAILED
        assert p._error == "import error: foo"


# ---------------------------------------------------------------------------
# status_summary
# ---------------------------------------------------------------------------


class TestStatusSummary:
    def test_returns_all_keys(self):
        p = _make_plugin()
        summary = p.status_summary()
        expected_keys = {
            "plugin_id", "version", "state", "generation", "loaded_at",
            "error", "match_count", "handle_count", "consume_count",
            "error_count", "consecutive_errors", "last_success_at",
            "last_error_at", "last_error_message", "timeout_count",
            "permission_denied_count", "total_match_ms", "total_handle_ms",
            "transition_count",
        }
        assert set(summary.keys()) == expected_keys

    def test_reflects_current_state(self):
        p = _make_plugin()
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")
        summary = p.status_summary()
        assert summary["state"] == "loaded"
        assert summary["transition_count"] == 2
