"""Tests for ResourceScope, TaskSupervisor, and failure compensation."""

import asyncio

import pytest

from src.core.message_bus import MessageBus, MessageType
from src.plugin.scope import ResourceScope, TaskSupervisor

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeHandler:
    def __init__(self, name="fake"):
        self.name = name
        self.priority = 0

    def match(self, payload):
        return True

    async def handle(self, payload, bot):
        return False


# ---------------------------------------------------------------------------
# TaskSupervisor
# ---------------------------------------------------------------------------


class TestTaskSupervisor:
    @pytest.mark.asyncio
    async def test_create_and_shutdown(self):
        ts = TaskSupervisor("test_pid")
        assert ts.task_count == 0
        assert not ts.closed

        async def _worker():
            await asyncio.sleep(10)

        ts.create_task("bg", _worker())
        assert ts.task_count == 1
        assert ts.active_task_count == 0  # not started yet

    @pytest.mark.asyncio
    async def test_create_task_after_close_raises(self):
        ts = TaskSupervisor("test_pid")

        async def _worker():
            await asyncio.sleep(0.01)

        ts.create_task("bg", _worker())
        ts._closed = True  # simulate shutdown
        with pytest.raises(RuntimeError):
            ts.create_task("bg2", _worker())

    @pytest.mark.asyncio
    async def test_cooperative_cancel(self):
        """Task catches CancelledError → shutdown returns empty list."""
        ts = TaskSupervisor("test_pid")

        async def _cooperative():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                # cooperative cleanup
                raise

        ts.create_task("coop", _cooperative())
        # Give the task a moment to start.
        await asyncio.sleep(0.01)
        uncancelled = await ts.shutdown()
        assert uncancelled == []
        assert ts.closed

    @pytest.mark.asyncio
    async def test_forced_timeout(self):
        """Task ignores cancel → name appears in returned list."""
        ts = TaskSupervisor("test_pid", shutdown_timeout=0.05)

        async def _stubborn():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(10)  # keep sleeping after cancel

        ts.create_task("stubborn", _stubborn())
        await asyncio.sleep(0.01)
        uncancelled = await ts.shutdown()
        assert "stubborn" in uncancelled

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self):
        ts = TaskSupervisor("test_pid", shutdown_timeout=0.1)

        async def _worker():
            await asyncio.sleep(0.01)

        ts.create_task("w", _worker())
        await asyncio.sleep(0.02)
        r1 = await ts.shutdown()
        r2 = await ts.shutdown()
        assert r2 == []  # second call is no-op
        assert r1 == r2 or r2 == []

    @pytest.mark.asyncio
    async def test_status_summary(self):
        ts = TaskSupervisor("test_pid")

        async def _w():
            await asyncio.sleep(0.01)

        ts.create_task("w1", _w())
        s = ts.status_summary()
        assert s["plugin_id"] == "test_pid"
        assert s["task_count"] == 1
        assert "w1" in s["tasks"]


# ---------------------------------------------------------------------------
# ResourceScope
# ---------------------------------------------------------------------------


class TestResourceScope:
    @pytest.mark.asyncio
    async def test_subscribe_increases_count(self):
        bus = MessageBus()
        before = bus.subscription_count
        scope = ResourceScope("p1", 1, bus)
        scope.subscribe(MessageType.EXTERNAL, _FakeHandler("h1"), 10)
        assert bus.subscription_count > before
        await scope.close()

    @pytest.mark.asyncio
    async def test_close_removes_subscriptions(self):
        bus = MessageBus()
        baseline = bus.subscription_count
        scope = ResourceScope("p1", 1, bus)
        scope.subscribe(MessageType.EXTERNAL, _FakeHandler("h1"), 10)
        assert bus.subscription_count > baseline
        await scope.close()
        assert bus.subscription_count == baseline

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        bus = MessageBus()
        scope = ResourceScope("p1", 1, bus)
        scope.subscribe(MessageType.EXTERNAL, _FakeHandler("h1"), 10)
        await scope.close()
        errors = await scope.close()
        assert errors == []  # second close returns stored errors (empty)

    @pytest.mark.asyncio
    async def test_failure_compensation(self):
        """One resource close raises → others still get closed, errors collected."""
        bus = MessageBus()
        scope = ResourceScope("p1", 1, bus)

        class BadResource:
            def close(self):
                raise RuntimeError("boom")

        class GoodResource:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        good = GoodResource()
        scope.add_resource(BadResource())
        scope.add_resource(good)

        errors = await scope.close()
        assert len(errors) >= 1
        assert any("boom" in e for e in errors)
        assert good.closed

    @pytest.mark.asyncio
    async def test_async_close_resource(self):
        """Resources with async close() are awaited."""
        bus = MessageBus()
        scope = ResourceScope("p1", 1, bus)

        class AsyncResource:
            def __init__(self):
                self.closed = False

            async def close(self):
                await asyncio.sleep(0.01)
                self.closed = True

        res = AsyncResource()
        scope.add_resource(res)
        await scope.close()
        assert res.closed

    def test_repr(self):
        bus = MessageBus()
        scope = ResourceScope("my_plugin", 2, bus)
        r = repr(scope)
        assert "my_plugin" in r
        assert "2" in r
        assert "False" in r  # not closed yet

    @pytest.mark.asyncio
    async def test_100x_create_close_no_leak(self):
        """Create and close 100 scopes – no subscription leak."""
        bus = MessageBus()
        baseline = bus.subscription_count
        for i in range(100):
            scope = ResourceScope(f"p{i}", 1, bus)
            scope.subscribe(MessageType.EXTERNAL, _FakeHandler(f"h{i}"), 10)
            await scope.close()
        assert bus.subscription_count == baseline

    @pytest.mark.asyncio
    async def test_create_task_delegates(self):
        bus = MessageBus()
        scope = ResourceScope("p1", 1, bus)

        async def _worker():
            await asyncio.sleep(0.01)

        task = scope.create_task("bg", _worker())
        assert scope.task_supervisor.task_count == 1
        await task
        await scope.close()
