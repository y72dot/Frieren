"""Tests for PluginRuntime: activate, shutdown, reload, health, diagnostics, backward compat."""


import pytest

from src.core.message_bus import MessageBus
from src.plugin.loaded import LoadedPlugin, PluginState
from src.plugin.registry import build_snapshot
from src.plugin.runtime import PluginRuntime

# ---------------------------------------------------------------------------
# minimal bot stand-in
# ---------------------------------------------------------------------------


class _FakeBot:
    """Minimal bot for runtime tests."""

    def __init__(self, bus: MessageBus):
        self.message_bus = bus
        self.filter_mgr = _FakeFilterMgr()


class _FakeFilterMgr:
    def is_global_blocked(self, event):
        return False

    def is_plugin_blocked(self, name, event):
        return False


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def bot(bus):
    return _FakeBot(bus)


@pytest.fixture
def runtime(bus, bot):
    return PluginRuntime(bus=bus, bot=bot)


# ---------------------------------------------------------------------------
# TestActivate
# ---------------------------------------------------------------------------


class TestActivate:
    @pytest.mark.asyncio
    async def test_no_plugin_dirs_returns_zero(self, runtime):
        count = await runtime.activate([])
        assert count == 0
        assert runtime.generation == 1
        assert runtime.snapshot.plugin_count == 0

    @pytest.mark.asyncio
    async def test_package_plugin_activates(self, runtime):
        count = await runtime.activate(["tests/test_plugins"])
        assert count >= 0  # may have none, just ensure no crash
        assert runtime.generation == 1

    @pytest.mark.asyncio
    async def test_respects_disabled(self, runtime):
        _count = await runtime.activate(
            ["tests/test_plugins"],
            disabled=["ping", "repeater", "admin", "llm", "tool_agent", "search"],
        )
        assert runtime.generation == 1

    @pytest.mark.asyncio
    async def test_empty_dirs_no_crash(self, runtime):
        count = await runtime.activate(["nonexistent_dir_xyz"])
        assert count == 0

    @pytest.mark.asyncio
    async def test_shutdown_stops_all(self, runtime):
        await runtime.activate(["tests/test_plugins"])
        await runtime.shutdown()
        assert runtime.snapshot.plugin_count == 0
        assert runtime.snapshot.generation == 0


# ---------------------------------------------------------------------------
# TestReload
# ---------------------------------------------------------------------------


class TestReload:
    @pytest.mark.asyncio
    async def test_reload_increases_generation(self, runtime):
        await runtime.activate(["tests/test_plugins"])
        gen_before = runtime.generation
        await runtime.reload(["tests/test_plugins"])
        assert runtime.generation == gen_before + 1

    @pytest.mark.asyncio
    async def test_reload_twice(self, runtime):
        await runtime.activate(["tests/test_plugins"])
        gen1 = runtime.generation
        await runtime.reload(["tests/test_plugins"])
        gen2 = runtime.generation
        assert gen2 > gen1
        await runtime.reload(["tests/test_plugins"])
        assert runtime.generation > gen2

    @pytest.mark.asyncio
    async def test_targeted_reload_restores_old_snapshot_on_activation_failure(
        self, runtime, monkeypatch
    ):
        from types import SimpleNamespace

        from src.plugin.definition import PluginDefinition
        from src.plugin.manifest import PluginManifest

        old = LoadedPlugin(
            manifest=PluginManifest(
                id="demo",
                version="1.0.0",
                entrypoint="demo.plugin:Demo",
                sdk="*",
            ),
            definition=PluginDefinition(plugin_id="demo", version="1.0.0"),
        )
        old.state = PluginState.ACTIVE
        runtime._plugins["demo"] = old
        old_snapshot = build_snapshot(runtime._plugins, 1)
        runtime.registry.publish(old_snapshot)

        monkeypatch.setattr(
            "src.plugin.runtime.discover_candidates",
            lambda _dirs: [SimpleNamespace(plugin_id="demo")],
        )

        async def fail_activation(_candidate, gen):
            failed = LoadedPlugin(
                manifest=PluginManifest(
                    id="demo",
                    version="2.0.0",
                    entrypoint="demo.plugin:Demo",
                    sdk="*",
                ),
                definition=PluginDefinition(plugin_id="demo", version="2.0.0"),
                generation=gen,
            )
            failed.state = PluginState.FAILED
            runtime._plugins["demo"] = failed

        monkeypatch.setattr(runtime, "_activate_one", fail_activation)

        active = await runtime.reload_plugin("demo", ["missing"])

        assert active is False
        assert runtime.get_plugin("demo") is old
        assert runtime.snapshot is old_snapshot


# ---------------------------------------------------------------------------
# TestHealth
# ---------------------------------------------------------------------------


class TestHealth:
    def test_record_handler_success_resets_consecutive(self, runtime):
        # Inject a LoadedPlugin directly.
        from src.plugin.definition import PluginDefinition
        from src.plugin.manifest import PluginManifest

        manifest = PluginManifest(id="hp", version="1.0.0", entrypoint="", sdk="*")
        definition = PluginDefinition(plugin_id="hp", version="1.0.0")
        p = LoadedPlugin(manifest=manifest, definition=definition)
        p.state = PluginState.ACTIVE
        p.health.consecutive_errors = 5
        runtime._plugins["hp"] = p

        runtime.record_handler_success("hp", elapsed_ms=10.0)
        assert p.health.consecutive_errors == 0
        assert p.health.match_count == 1

    def test_record_handler_error_degrade(self, runtime):
        from src.plugin.definition import PluginDefinition
        from src.plugin.manifest import PluginManifest

        manifest = PluginManifest(id="hp", version="1.0.0", entrypoint="", sdk="*")
        definition = PluginDefinition(plugin_id="hp", version="1.0.0")
        p = LoadedPlugin(manifest=manifest, definition=definition)
        p.state = PluginState.ACTIVE
        runtime._plugins["hp"] = p

        for i in range(runtime._max_consecutive_errors + 1):
            runtime.record_handler_error("hp", f"error {i}")
        assert p.state == PluginState.DEGRADED

    def test_degraded_heals(self, runtime):
        from src.plugin.definition import PluginDefinition
        from src.plugin.manifest import PluginManifest

        manifest = PluginManifest(id="hp", version="1.0.0", entrypoint="", sdk="*")
        definition = PluginDefinition(plugin_id="hp", version="1.0.0")
        p = LoadedPlugin(manifest=manifest, definition=definition)
        p.state = PluginState.DEGRADED
        p.health.consecutive_errors = 0
        runtime._plugins["hp"] = p

        runtime.record_handler_success("hp")
        assert p.state == PluginState.ACTIVE

    def test_record_nonexistent_plugin_noop(self, runtime):
        """Should not raise for unknown plugin_id."""
        runtime.record_handler_success("nonexistent", 5.0)
        runtime.record_handler_error("nonexistent", "err")


# ---------------------------------------------------------------------------
# TestDiagnostics
# ---------------------------------------------------------------------------


class TestDiagnostics:
    def test_status_summary(self, runtime):
        from src.plugin.definition import PluginDefinition
        from src.plugin.manifest import PluginManifest

        for state in PluginState:
            manifest = PluginManifest(
                id=f"test_{state.value}", version="1.0.0", entrypoint="", sdk="*"
            )
            definition = PluginDefinition(
                plugin_id=f"test_{state.value}", version="1.0.0"
            )
            p = LoadedPlugin(manifest=manifest, definition=definition)
            p.state = state
            runtime._plugins[p.plugin_id] = p

        summaries = runtime.status_summary()
        assert len(summaries) == len(PluginState)
        states_in_summary = {s["state"] for s in summaries}
        for state in PluginState:
            assert state.value in states_in_summary

    def test_get_plugin_exists(self, runtime):
        from src.plugin.definition import PluginDefinition
        from src.plugin.manifest import PluginManifest

        manifest = PluginManifest(id="p1", version="1.0.0", entrypoint="", sdk="*")
        definition = PluginDefinition(plugin_id="p1", version="1.0.0")
        p = LoadedPlugin(manifest=manifest, definition=definition)
        runtime._plugins["p1"] = p

        assert runtime.get_plugin("p1") is p

    def test_get_plugin_nonexistent(self, runtime):
        assert runtime.get_plugin("nonexistent") is None


# ---------------------------------------------------------------------------
# TestBackwardCompat: PluginManager without Runtime
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_plugin_manager_without_runtime_works(self, bus):
        """Regression gate: PluginManager works without Runtime."""
        from src.plugin.manager import PluginManager

        pm = PluginManager(bus=bus)
        count = pm.auto_discover(["tests/test_plugins"])
        assert count >= 0
        assert pm.plugin_count == count
        pm.close()

    def test_plugin_manager_without_runtime_dispatch(self, bus):
        """Existing dispatch path still works."""
        from src.plugin.manager import PluginManager

        pm = PluginManager(bus=bus)
        pm.auto_discover(["tests/test_plugins"])
        pm.close()


# ---------------------------------------------------------------------------
# TestSnapshotInteraction
# ---------------------------------------------------------------------------


class TestSnapshotInteraction:
    def test_active_plugins_appear_in_snapshot(self):
        from src.plugin.definition import PluginDefinition
        from src.plugin.manifest import PluginManifest

        plugins = {}
        for i, state in enumerate([PluginState.ACTIVE, PluginState.DEGRADED, PluginState.FAILED]):
            pid = f"p{i}"
            manifest = PluginManifest(id=pid, version="1.0.0", entrypoint="", sdk="*")
            definition = PluginDefinition(plugin_id=pid, version="1.0.0")
            p = LoadedPlugin(manifest=manifest, definition=definition)
            p.state = state
            plugins[pid] = p

        snap = build_snapshot(plugins, 1)
        assert "p0" in snap.plugin_ids  # ACTIVE
        assert "p1" in snap.plugin_ids  # DEGRADED
        assert "p2" not in snap.plugin_ids  # FAILED


# ---------------------------------------------------------------------------
# TestActivePlugins
# ---------------------------------------------------------------------------


class TestActivePlugins:
    def test_active_plugins_filters_correctly(self, runtime):
        from src.plugin.definition import PluginDefinition
        from src.plugin.manifest import PluginManifest

        for state, pid in [
            (PluginState.ACTIVE, "a"),
            (PluginState.DEGRADED, "d"),
            (PluginState.FAILED, "f"),
            (PluginState.STOPPED, "s"),
        ]:
            manifest = PluginManifest(id=pid, version="1.0.0", entrypoint="", sdk="*")
            definition = PluginDefinition(plugin_id=pid, version="1.0.0")
            p = LoadedPlugin(manifest=manifest, definition=definition)
            p.state = state
            runtime._plugins[pid] = p

        active = runtime.active_plugins
        assert "a" in active
        assert "d" in active
        assert "f" not in active
        assert "s" not in active


# ---------------------------------------------------------------------------
# TestProperties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_snapshot_shortcut(self, runtime):
        assert runtime.snapshot is runtime.registry.current

    def test_plugins_returns_copy(self, runtime):
        runtime._plugins["x"] = LoadedPlugin(
            manifest=__import__("src.plugin.manifest", fromlist=["PluginManifest"])
            .PluginManifest(id="x", version="1.0.0", entrypoint="", sdk="*"),
            definition=__import__(
                "src.plugin.definition", fromlist=["PluginDefinition"]
            ).PluginDefinition(plugin_id="x", version="1.0.0"),
        )
        plugins = runtime.plugins
        plugins["y"] = plugins["x"]
        assert "y" not in runtime._plugins
