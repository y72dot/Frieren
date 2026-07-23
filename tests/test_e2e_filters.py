"""E2E filter pipeline tests: global + per-plugin filtering through the bus."""

from __future__ import annotations

import pytest

from src.core.config import (
    BotConfig,
    FilterConfig,
    FilterModeConfig,
    PluginFilterConfig,
)
from src.core.message_bus import MessageType
from src.plugin.base import Event
from tests.conftest_e2e import dispatch_raw_event, e2e_bot  # noqa: F401

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _set_filter_config(bot, **kwargs) -> None:
    """Replace bot.config.filter with a new FilterConfig built from kwargs."""
    cfg = bot.config
    bot.filter_mgr.update_config(
        BotConfig(
            bot=cfg.bot,
            napcat=cfg.napcat,
            plugin=cfg.plugin,
            logging=cfg.logging,
            filter=FilterConfig(**kwargs),
            env=cfg.env,
        )
    )


def _plugin_was_called_collector():
    """Return (plugin_instance, called_flag) for verifying plugin invocation."""
    called = False

    class _P:
        name = "filter_test_plugin"
        priority = 0

        def match(self, event: Event) -> bool:
            return True

        async def handle(self, event: Event, bot) -> bool:
            nonlocal called
            called = True
            return True

    return _P(), lambda: called


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGlobalFilters:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_global_blacklist_blocks(self, e2e_bot):
        """Blacklisted group event is blocked before any plugin sees it."""
        _set_filter_config(
            e2e_bot,
            enable=True,
            group=FilterModeConfig(mode="blacklist", list=[456]),
        )
        plugin, was_called = _plugin_was_called_collector()
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, plugin, 0)

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 999,
            "group_id": 456,
            "raw_message": "hello",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert was_called() is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_global_whitelist_allows_only(self, e2e_bot):
        """Non-whitelisted group is blocked; whitelisted group passes."""
        _set_filter_config(
            e2e_bot,
            enable=True,
            group=FilterModeConfig(mode="whitelist", list=[999]),
        )
        plugin, was_called = _plugin_was_called_collector()
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, plugin, 0)

        # Non-whitelisted → blocked
        raw_bad = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 888,
            "group_id": 456,
            "raw_message": "hello",
            "message_id": 1,
        }
        await dispatch_raw_event(e2e_bot, raw_bad)
        assert was_called() is False

        # Whitelisted → passes
        called2 = False

        class _P2:
            name = "filter_test_plugin"
            priority = 0

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                nonlocal called2
                called2 = True
                return True

        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, _P2(), 0)
        raw_good = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 222,
            "group_id": 999,
            "raw_message": "hi",
            "message_id": 2,
        }
        await dispatch_raw_event(e2e_bot, raw_good)
        assert called2 is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_admin_bypass_all_filters(self, e2e_bot):
        """Admin user bypasses all global filters."""
        _set_filter_config(
            e2e_bot,
            enable=True,
            group=FilterModeConfig(mode="blacklist", list=[456]),
        )
        plugin, was_called = _plugin_was_called_collector()
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, plugin, 0)

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,  # admin user from bot_config fixture
            "group_id": 456,  # blacklisted group
            "raw_message": "hello",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert was_called() is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_bot_self_bypass(self, e2e_bot):
        """Bot's own user_id bypasses all filters."""
        _set_filter_config(
            e2e_bot,
            enable=True,
            group=FilterModeConfig(mode="blacklist", list=[456]),
        )
        plugin, was_called = _plugin_was_called_collector()
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, plugin, 0)

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 123456,  # bot's own QQ (from bot_config fixture)
            "group_id": 456,  # blacklisted group
            "raw_message": "hello",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert was_called() is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_filter_disabled(self, e2e_bot):
        """When filter.enable=False, all events pass through."""
        _set_filter_config(
            e2e_bot,
            enable=False,
            group=FilterModeConfig(mode="blacklist", list=[456]),
        )
        plugin, was_called = _plugin_was_called_collector()
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, plugin, 0)

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 111,
            "group_id": 456,
            "raw_message": "hello",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert was_called() is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_notice_bypass_filters(self, e2e_bot):
        """Notice events are never filtered (only message events are)."""
        _set_filter_config(
            e2e_bot,
            enable=True,
            group=FilterModeConfig(mode="blacklist", list=[456]),
        )
        plugin, was_called = _plugin_was_called_collector()
        # Use a notice-matching plugin
        plugin.name = "notice_plugin"

        def orig_match(event):
            return event.type.startswith("notice.")

        plugin.match = orig_match
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, plugin, 0)

        raw = {
            "post_type": "notice",
            "notice_type": "group_increase",
            "user_id": 999,
            "group_id": 456,
        }

        await dispatch_raw_event(e2e_bot, raw)
        assert was_called() is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_private_filter_separate(self, e2e_bot):
        """Private messages use private filter config, not group config."""
        # Block a user via private blacklist, but allow all groups
        _set_filter_config(
            e2e_bot,
            enable=True,
            private=FilterModeConfig(mode="blacklist", list=[789]),
        )
        plugin, was_called = _plugin_was_called_collector()
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, plugin, 0)

        # Blocked private user
        raw_blocked = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 789,
            "raw_message": "hi",
            "message_id": 1,
        }
        await dispatch_raw_event(e2e_bot, raw_blocked)
        assert was_called() is False

        # Allowed private user
        called2 = False

        class _P2:
            name = "filter_test_plugin"
            priority = 0

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                nonlocal called2
                called2 = True
                return True

        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, _P2(), 0)
        raw_ok = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 111,
            "raw_message": "hello",
            "message_id": 2,
        }
        await dispatch_raw_event(e2e_bot, raw_ok)
        assert called2 is True


class TestPerPluginFilters:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_per_plugin_filter(self, e2e_bot):
        """Per-plugin blacklist skips only the targeted plugin."""
        _set_filter_config(
            e2e_bot,
            enable=True,
            plugins={
                "blocked_plugin": PluginFilterConfig(
                    enable=True,
                    group=FilterModeConfig(mode="blacklist", list=[456]),
                ),
            },
        )

        blocked_called = False

        class _BlockedPlugin:
            name = "blocked_plugin"
            priority = 5

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                nonlocal blocked_called
                blocked_called = True
                return True

        unblocked_called = False

        class _UnblockedPlugin:
            name = "unblocked_plugin"
            priority = 10

            def match(self, event: Event) -> bool:
                return True

            async def handle(self, event: Event, bot) -> bool:
                nonlocal unblocked_called
                unblocked_called = True
                return True

        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, _BlockedPlugin(), 5)
        e2e_bot.message_bus.subscribe(MessageType.EXTERNAL, _UnblockedPlugin(), 10)

        raw = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 999,
            "group_id": 456,
            "raw_message": "hello",
            "message_id": 1,
        }

        await dispatch_raw_event(e2e_bot, raw)

        assert blocked_called is False
        assert unblocked_called is True
