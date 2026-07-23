"""Tests for LifecycleRunner: hook execution, timeout, compensation, error handling."""

import asyncio

import pytest

from src.plugin.definition import LifecycleHookSpec, PluginDefinition
from src.plugin.lifecycle import LifecycleHookResult, LifecycleResult, LifecycleRunner
from src.plugin.loaded import LoadedPlugin, PluginState
from src.plugin.manifest import PluginManifest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_manifest(plugin_id: str = "test_plugin") -> PluginManifest:
    return PluginManifest(id=plugin_id, version="1.0.0", entrypoint="", sdk="*")


def _make_plugin(
    plugin_id: str = "test_plugin",
    hooks: tuple[LifecycleHookSpec, ...] = (),
) -> LoadedPlugin:
    manifest = _make_manifest(plugin_id)
    definition = PluginDefinition(
        plugin_id=plugin_id,
        version="1.0.0",
        lifecycle_hooks=hooks,
    )
    return LoadedPlugin(manifest=manifest, definition=definition)


# ---------------------------------------------------------------------------
# mock bot
# ---------------------------------------------------------------------------


class _FakeBot:
    """Minimal bot stand-in for lifecycle tests."""
    pass


# ---------------------------------------------------------------------------
# LifecycleResult properties
# ---------------------------------------------------------------------------


class TestLifecycleResult:
    def test_failed_hooks(self):
        r = LifecycleResult(phase="setup", success=False)
        r.results.append(LifecycleHookResult(hook_type="setup", success=True, elapsed_ms=1.0))
        r.results.append(LifecycleHookResult(hook_type="setup", success=False, elapsed_ms=2.0, error="boom"))
        assert len(r.failed_hooks) == 1
        assert r.failed_hooks[0].error == "boom"


# ---------------------------------------------------------------------------
# LifecycleRunner
# ---------------------------------------------------------------------------


class TestRunPhase:
    @pytest.mark.asyncio
    async def test_empty_hooks_returns_success(self):
        runner = LifecycleRunner()
        p = _make_plugin()
        # Advance to LOADED so it's in a realistic state.
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")
        result = await runner.run_phase(p, "setup", _FakeBot())
        assert result.success
        assert result.results == []

    @pytest.mark.asyncio
    async def test_all_hooks_succeed(self):
        called = []

        async def hook_a(bot):
            called.append("a")

        async def hook_b(bot):
            called.append("b")

        hooks = (
            LifecycleHookSpec(hook_type="setup", handler=hook_a),
            LifecycleHookSpec(hook_type="setup", handler=hook_b),
        )
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        result = await runner.run_phase(p, "setup", _FakeBot())
        assert result.success
        assert called == ["a", "b"]
        assert len(result.results) == 2
        assert all(r.success for r in result.results)
        assert result.total_elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_one_fails_others_still_run(self):
        called = []

        async def hook_ok(bot):
            called.append("ok")

        async def hook_fail(bot):
            called.append("fail")
            raise RuntimeError("boom")

        async def hook_also_ok(bot):
            called.append("also_ok")

        hooks = (
            LifecycleHookSpec(hook_type="setup", handler=hook_ok),
            LifecycleHookSpec(hook_type="setup", handler=hook_fail),
            LifecycleHookSpec(hook_type="setup", handler=hook_also_ok),
        )
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        result = await runner.run_phase(p, "setup", _FakeBot())
        assert not result.success
        assert called == ["ok", "fail", "also_ok"]
        assert not result.results[1].success
        assert result.results[1].error == "boom"

    @pytest.mark.asyncio
    async def test_hook_timeout(self):
        async def hook_slow(bot):
            await asyncio.sleep(10)

        hooks = (LifecycleHookSpec(hook_type="setup", handler=hook_slow),)
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner(setup_timeout=0.05)
        result = await runner.run_phase(p, "setup", _FakeBot())
        assert not result.success
        assert not result.results[0].success
        assert "Timeout" in result.results[0].error

    @pytest.mark.asyncio
    async def test_hook_cancelled_error_propagated(self):
        """CancelledError is re-raised, not caught as general Exception."""
        async def hook_cancel(bot):
            raise asyncio.CancelledError()

        hooks = (LifecycleHookSpec(hook_type="setup", handler=hook_cancel),)
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        with pytest.raises(asyncio.CancelledError):
            await runner.run_phase(p, "setup", _FakeBot())


class TestSetupAndStart:
    @pytest.mark.asyncio
    async def test_setup_fails_triggers_compensation(self):
        stop_called = []

        async def hook_setup_fail(bot):
            raise RuntimeError("setup failed")

        async def hook_stop(bot):
            stop_called.append(True)

        hooks = (
            LifecycleHookSpec(hook_type="setup", handler=hook_setup_fail),
            LifecycleHookSpec(hook_type="stop", handler=hook_stop),
        )
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        result = await runner.setup_and_start(p, _FakeBot())
        assert not result.success
        assert p.state == PluginState.FAILED
        assert len(stop_called) == 1

    @pytest.mark.asyncio
    async def test_start_fails_triggers_compensation(self):
        stop_called = []

        async def hook_setup(bot):
            pass

        async def hook_start_fail(bot):
            raise RuntimeError("start failed")

        async def hook_stop(bot):
            stop_called.append(True)

        hooks = (
            LifecycleHookSpec(hook_type="setup", handler=hook_setup),
            LifecycleHookSpec(hook_type="start", handler=hook_start_fail),
            LifecycleHookSpec(hook_type="stop", handler=hook_stop),
        )
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        result = await runner.setup_and_start(p, _FakeBot())
        assert not result.success
        assert p.state == PluginState.FAILED
        assert len(stop_called) == 1

    @pytest.mark.asyncio
    async def test_setup_and_start_success(self):
        called = []

        async def hook_setup(bot):
            called.append("setup")

        async def hook_start(bot):
            called.append("start")

        hooks = (
            LifecycleHookSpec(hook_type="setup", handler=hook_setup),
            LifecycleHookSpec(hook_type="start", handler=hook_start),
        )
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        result = await runner.setup_and_start(p, _FakeBot())
        assert result.success
        assert called == ["setup", "start"]


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_errors_non_fatal(self):
        """Stop phase always returns success=True even if hooks fail."""
        async def hook_stop_fail(bot):
            raise RuntimeError("stop failed")

        hooks = (LifecycleHookSpec(hook_type="stop", handler=hook_stop_fail),)
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        result = await runner.stop(p, _FakeBot())
        assert result.success  # phase always succeeds


class TestBotParameter:
    @pytest.mark.asyncio
    async def test_context_passed_to_handler(self):
        bot = _FakeBot()
        received = []

        async def hook(ctx):
            received.append(ctx)

        hooks = (LifecycleHookSpec(hook_type="setup", handler=hook),)
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")
        # Set up a minimal context since lifecycle always passes ctx now.
        from src.plugin.context import PluginConfigView, PluginContext
        cfg = PluginConfigView(bot_id=0, nickname="test", admin_users=())
        p.context = PluginContext(
            plugin_id=p.manifest.id,
            version=p.manifest.version,
            generation=p.generation,
            permissions=p.manifest.permissions,
            _bot=None,
            config=cfg,
        )

        runner = LifecycleRunner()
        await runner.run_phase(p, "setup", bot)
        assert received[0] is p.context


class TestSyncHandler:
    @pytest.mark.asyncio
    async def test_sync_handler_supported(self):
        called = []

        def hook_sync(bot):
            called.append(True)

        hooks = (LifecycleHookSpec(hook_type="setup", handler=hook_sync),)
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        result = await runner.run_phase(p, "setup", _FakeBot())
        assert result.success
        assert len(called) == 1


class TestElapsedMs:
    @pytest.mark.asyncio
    async def test_elapsed_ms_tracked(self):
        async def hook(bot):
            await asyncio.sleep(0.02)

        hooks = (LifecycleHookSpec(hook_type="setup", handler=hook),)
        p = _make_plugin(hooks=hooks)
        p.transition(PluginState.VALIDATED, "ok")
        p.transition(PluginState.LOADED, "imported")

        runner = LifecycleRunner()
        result = await runner.run_phase(p, "setup", _FakeBot())
        assert result.results[0].elapsed_ms >= 20
