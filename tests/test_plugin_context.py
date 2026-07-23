"""Tests for PluginContext, QQAgency, PluginConfigView, PermissionDeniedError."""

import asyncio

import pytest

from src.plugin.context import (
    PermissionDeniedError,
    PluginConfigView,
    PluginContext,
    QQAgency,
)
from src.plugin.manifest import ManifestPermissions

# ---------------------------------------------------------------------------
# Fake API client for testing QQAgency
# ---------------------------------------------------------------------------


class FakeApiClient:
    """Records calls so we can verify QQAgency delegates correctly."""

    def __init__(self):
        self.calls: list[dict] = []

    async def send_group_msg(self, group_id: int, message: str) -> dict:
        self.calls.append({"method": "send_group_msg", "group_id": group_id, "message": message})
        return {"status": "ok", "message_id": 123}

    async def send_private_msg(self, user_id: int, message: str) -> dict:
        self.calls.append({"method": "send_private_msg", "user_id": user_id, "message": message})
        return {"status": "ok", "message_id": 456}

    async def send_group_poke(self, group_id: int, user_id: int) -> dict:
        self.calls.append({"method": "send_group_poke", "group_id": group_id, "user_id": user_id})
        return {"status": "ok"}

    async def call_action(self, action: str, **params):
        self.calls.append({"method": "call_action", "action": action, "params": params})
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _perms(**kwargs):
    """Build ManifestPermissions with defaults."""
    defaults = {"qq": [], "storage": [], "scheduler": False, "network": []}
    defaults.update(kwargs)
    return ManifestPermissions(**defaults)


# ---------------------------------------------------------------------------
# PermissionDeniedError
# ---------------------------------------------------------------------------


class TestPermissionDeniedError:
    def test_contains_plugin_id_and_permission(self):
        err = PermissionDeniedError("test_plug", "message.send")
        assert err.plugin_id == "test_plug"
        assert err.permission == "message.send"
        assert "test_plug" in str(err)
        assert "message.send" in str(err)

    def test_detail_field(self):
        err = PermissionDeniedError("p1", "message.react", detail="no react allowed")
        assert err.detail == "no react allowed"
        assert "no react allowed" in str(err)

    def test_is_runtime_error(self):
        err = PermissionDeniedError("p", "x")
        assert isinstance(err, RuntimeError)


# ---------------------------------------------------------------------------
# PluginConfigView
# ---------------------------------------------------------------------------


class TestPluginConfigView:
    def test_construction(self):
        view = PluginConfigView(bot_id=100, nickname="TestBot", admin_users=(1, 2))
        assert view.bot_id == 100
        assert view.nickname == "TestBot"
        assert view.admin_users == (1, 2)

    def test_is_frozen(self):
        view = PluginConfigView(bot_id=100, nickname="Bot", admin_users=())
        with pytest.raises(Exception):
            view.bot_id = 200  # type: ignore[misc]

    def test_defaults(self):
        view = PluginConfigView(bot_id=0, nickname="", admin_users=())
        assert view.bot_id == 0
        assert view.nickname == ""
        assert view.admin_users == ()


# ---------------------------------------------------------------------------
# QQAgency
# ---------------------------------------------------------------------------


class TestQQAgency:
    @pytest.mark.asyncio
    async def test_send_group_msg_allowed(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["message.send"]), "p1")
        result = await agency.send_group_msg(123, "hello")
        assert result["status"] == "ok"
        assert api.calls[0]["method"] == "send_group_msg"

    @pytest.mark.asyncio
    async def test_send_group_msg_denied(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=[]), "p1")
        with pytest.raises(PermissionDeniedError) as exc:
            await agency.send_group_msg(123, "hello")
        assert exc.value.permission == "message.send"
        assert exc.value.plugin_id == "p1"
        assert len(api.calls) == 0

    @pytest.mark.asyncio
    async def test_send_private_msg_allowed(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["message.send"]), "p2")
        result = await agency.send_private_msg(456, "hi")
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_send_private_msg_denied(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["group.manage"]), "p2")
        with pytest.raises(PermissionDeniedError):
            await agency.send_private_msg(456, "hi")

    @pytest.mark.asyncio
    async def test_send_group_poke_allowed(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["message.react"]), "p3")
        result = await agency.send_group_poke(1, 2)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_send_group_poke_denied(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["message.send"]), "p3")
        with pytest.raises(PermissionDeniedError) as exc:
            await agency.send_group_poke(1, 2)
        assert exc.value.permission == "message.react"

    @pytest.mark.asyncio
    async def test_call_action_allowed(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["message.react"]), "p4")
        result = await agency.call_action("set_msg_emoji_like", message_id=1)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_call_action_denied(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=[]), "p4")
        with pytest.raises(PermissionDeniedError):
            await agency.call_action("some_action")

    @pytest.mark.asyncio
    async def test_empty_permissions_denies_all(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(), "p5")
        with pytest.raises(PermissionDeniedError):
            await agency.send_group_msg(1, "x")
        with pytest.raises(PermissionDeniedError):
            await agency.send_private_msg(1, "x")
        with pytest.raises(PermissionDeniedError):
            await agency.send_group_poke(1, 1)
        with pytest.raises(PermissionDeniedError):
            await agency.call_action("x")

    @pytest.mark.asyncio
    async def test_multiple_checks_dont_interfere(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["message.send"]), "p6")
        # First call succeeds.
        await agency.send_group_msg(1, "a")
        # Second call also succeeds.
        await agency.send_private_msg(1, "b")
        assert len(api.calls) == 2


# ---------------------------------------------------------------------------
# PluginContext
# ---------------------------------------------------------------------------


class _FakeBus:
    """Minimal bus stub for PluginContext.emit_internal."""

    def __init__(self):
        self.dispatched: list = []

    async def dispatch(self, msg, bot):
        self.dispatched.append((msg, bot))


class _FakeScope:
    """Minimal scope stub for PluginContext.create_task."""

    def __init__(self):
        self.tasks: list = []

    def create_task(self, name, coro):
        task = asyncio.create_task(coro)
        self.tasks.append((name, task))
        return task


class _FakeEvent:
    """Minimal event for PluginContext.reply."""

    def __init__(self, group_id=None, user_id=0):
        self.group_id = group_id
        self.user_id = user_id


class TestPluginContext:
    def test_construction_with_all_fields(self):
        api = FakeApiClient()
        config = PluginConfigView(bot_id=100, nickname="B", admin_users=(1,))
        ctx = PluginContext(
            plugin_id="test",
            version="1.0.0",
            generation=1,
            permissions=_perms(qq=["message.send"]),
            api=QQAgency(api, _perms(qq=["message.send"]), "test"),
            config=config,
        )
        assert ctx.plugin_id == "test"
        assert ctx.version == "1.0.0"
        assert ctx.generation == 1
        assert ctx.api is not None
        assert ctx.config is not None

    @pytest.mark.asyncio
    async def test_reply_routes_to_group(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["message.send"]), "p1")
        ctx = PluginContext(
            plugin_id="p1", version="1", generation=1,
            permissions=_perms(qq=["message.send"]), api=agency,
        )
        event = _FakeEvent(group_id=789, user_id=111)
        result = await ctx.reply(event, "hello group")
        assert result is True
        assert api.calls[0]["method"] == "send_group_msg"
        assert api.calls[0]["group_id"] == 789

    @pytest.mark.asyncio
    async def test_reply_routes_to_private(self):
        api = FakeApiClient()
        agency = QQAgency(api, _perms(qq=["message.send"]), "p1")
        ctx = PluginContext(
            plugin_id="p1", version="1", generation=1,
            permissions=_perms(qq=["message.send"]), api=agency,
        )
        event = _FakeEvent(group_id=None, user_id=222)
        result = await ctx.reply(event, "hello private")
        assert result is True
        assert api.calls[0]["method"] == "send_private_msg"
        assert api.calls[0]["user_id"] == 222

    @pytest.mark.asyncio
    async def test_reply_returns_false_without_api(self):
        ctx = PluginContext(
            plugin_id="p1", version="1", generation=1,
            permissions=_perms(),
        )
        event = _FakeEvent(group_id=1, user_id=1)
        result = await ctx.reply(event, "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_emit_internal_publishes(self):
        bus = _FakeBus()
        bot = object()
        ctx = PluginContext(
            plugin_id="p1", version="1", generation=1,
            permissions=_perms(), _bus=bus, _bot=bot,
        )
        ctx.emit_internal("test.topic", {"key": "val"})
        # emit_internal is fire-and-forget via create_task; wait briefly.
        import asyncio

        await asyncio.sleep(0.1)
        assert len(bus.dispatched) == 1
        msg, b = bus.dispatched[0]
        assert msg.type.value == "internal"
        assert msg.payload["topic"] == "test.topic"
        assert msg.payload["data"] == {"key": "val"}

    @pytest.mark.asyncio
    async def test_emit_internal_noop_without_bus(self):
        ctx = PluginContext(
            plugin_id="p1", version="1", generation=1,
            permissions=_perms(),
        )
        # Should not raise.
        ctx.emit_internal("t", {})

    @pytest.mark.asyncio
    async def test_create_task_delegates_to_scope(self):
        scope = _FakeScope()
        ctx = PluginContext(
            plugin_id="p1", version="1", generation=1,
            permissions=_perms(), _scope=scope,
        )

        async def dummy():
            return 42

        task = ctx.create_task("my_task", dummy())
        assert task is not None
        assert len(scope.tasks) == 1
        assert scope.tasks[0][0] == "my_task"

    @pytest.mark.asyncio
    async def test_create_task_raises_without_scope(self):
        ctx = PluginContext(
            plugin_id="p1", version="1", generation=1, permissions=_perms(),
        )
        with pytest.raises(RuntimeError, match="no ResourceScope"):

            async def dummy():
                pass

            ctx.create_task("t", dummy())

    def test_no_raw_bot_access(self):
        ctx = PluginContext(
            plugin_id="p1", version="1", generation=1, permissions=_perms(),
        )
        # PluginContext does NOT expose bot internals.
        assert not hasattr(ctx, "message_bus")
        assert not hasattr(ctx, "config_center")
        assert not hasattr(ctx, "tool_catalog")
        assert not hasattr(ctx, "filter_mgr")
