"""Tests for FilterManager subsystem."""

from __future__ import annotations

import pytest

from src.core.config import (
    BotConfig,
    BotConfigSection,
    FilterConfig,
    FilterModeConfig,
    NapCatConfig,
    PluginConfig,
    PluginFilterConfig,
    LoggingConfigSection,
)
from src.core.filter_manager import FilterManager
from src.plugin.base import Event


# -------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------


def _make_config(
    *,
    enable: bool = True,
    group_mode: str = "blacklist",
    group_list: list[int] | None = None,
    private_mode: str = "blacklist",
    private_list: list[int] | None = None,
    plugins: dict[str, PluginFilterConfig] | None = None,
    qq: int = 123456,
    admin_users: list[int] | None = None,
) -> BotConfig:
    return BotConfig(
        bot=BotConfigSection(qq=qq, nickname=[], admin_users=admin_users or []),
        napcat=NapCatConfig(),
        plugin=PluginConfig(),
        logging=LoggingConfigSection(),
        filter=FilterConfig(
            enable=enable,
            group=FilterModeConfig(mode=group_mode, list=group_list or []),
            private=FilterModeConfig(mode=private_mode, list=private_list or []),
            plugins=plugins or {},
        ),
    )


# -------------------------------------------------------------------
# is_global_blocked – message type gating
# -------------------------------------------------------------------


class TestGlobalBlockedMessageTypes:
    def test_group_message_checked(self):
        mgr = FilterManager(_make_config(group_mode="blacklist", group_list=[100]))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is True

    def test_private_message_checked(self):
        mgr = FilterManager(_make_config(private_mode="blacklist", private_list=[555]))
        event = Event(type="message.private", user_id=555, message="x", is_group=False)
        assert mgr.is_global_blocked(event) is True

    def test_notice_passes(self):
        mgr = FilterManager(_make_config(group_mode="whitelist", group_list=[]))
        event = Event(type="notice.notify", user_id=1, group_id=100)
        assert mgr.is_global_blocked(event) is False

    def test_request_passes(self):
        mgr = FilterManager(_make_config(group_mode="whitelist", group_list=[]))
        event = Event(type="request.friend", user_id=1)
        assert mgr.is_global_blocked(event) is False

    def test_meta_passes(self):
        mgr = FilterManager(_make_config())
        event = Event(type="meta.heartbeat", user_id=0)
        assert mgr.is_global_blocked(event) is False


# -------------------------------------------------------------------
# is_global_blocked – enable flag
# -------------------------------------------------------------------


class TestGlobalBlockedEnable:
    def test_disabled_passes_all(self):
        mgr = FilterManager(_make_config(enable=False, group_mode="blacklist", group_list=[100]))
        event = Event(type="message.group", user_id=999, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False


# -------------------------------------------------------------------
# is_global_blocked – blacklist mode
# -------------------------------------------------------------------


class TestGlobalBlacklist:
    def test_group_hit_blocked(self):
        mgr = FilterManager(_make_config(group_mode="blacklist", group_list=[100, 200]))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is True

    def test_group_miss_passes(self):
        mgr = FilterManager(_make_config(group_mode="blacklist", group_list=[100]))
        event = Event(type="message.group", user_id=1, message="x", group_id=999, is_group=True)
        assert mgr.is_global_blocked(event) is False

    def test_private_hit_blocked(self):
        mgr = FilterManager(_make_config(private_mode="blacklist", private_list=[555]))
        event = Event(type="message.private", user_id=555, message="x", is_group=False)
        assert mgr.is_global_blocked(event) is True

    def test_private_miss_passes(self):
        mgr = FilterManager(_make_config(private_mode="blacklist", private_list=[555]))
        event = Event(type="message.private", user_id=999, message="x", is_group=False)
        assert mgr.is_global_blocked(event) is False

    def test_empty_blacklist_passes_all(self):
        mgr = FilterManager(_make_config(group_mode="blacklist", group_list=[]))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False


# -------------------------------------------------------------------
# is_global_blocked – whitelist mode
# -------------------------------------------------------------------


class TestGlobalWhitelist:
    def test_group_hit_passes(self):
        mgr = FilterManager(_make_config(group_mode="whitelist", group_list=[100]))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False

    def test_group_miss_blocked(self):
        mgr = FilterManager(_make_config(group_mode="whitelist", group_list=[100]))
        event = Event(type="message.group", user_id=1, message="x", group_id=999, is_group=True)
        assert mgr.is_global_blocked(event) is True

    def test_private_hit_passes(self):
        mgr = FilterManager(_make_config(private_mode="whitelist", private_list=[777]))
        event = Event(type="message.private", user_id=777, message="x", is_group=False)
        assert mgr.is_global_blocked(event) is False

    def test_private_miss_blocked(self):
        mgr = FilterManager(_make_config(private_mode="whitelist", private_list=[777]))
        event = Event(type="message.private", user_id=999, message="x", is_group=False)
        assert mgr.is_global_blocked(event) is True

    def test_empty_whitelist_blocks_all(self):
        mgr = FilterManager(_make_config(group_mode="whitelist", group_list=[]))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is True


# -------------------------------------------------------------------
# is_global_blocked – mode="off"
# -------------------------------------------------------------------


class TestGlobalModeOff:
    def test_group_off_passes_all(self):
        mgr = FilterManager(_make_config(group_mode="off"))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False

    def test_private_off_passes_all(self):
        mgr = FilterManager(_make_config(private_mode="off"))
        event = Event(type="message.private", user_id=1, message="x", is_group=False)
        assert mgr.is_global_blocked(event) is False


# -------------------------------------------------------------------
# bypass – admin / bot self
# -------------------------------------------------------------------


class TestBypass:
    def test_admin_bypasses_group_blacklist(self):
        mgr = FilterManager(_make_config(
            group_mode="blacklist", group_list=[100], admin_users=[555]
        ))
        event = Event(type="message.group", user_id=555, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False

    def test_admin_bypasses_private_blacklist(self):
        mgr = FilterManager(_make_config(
            private_mode="blacklist", private_list=[555], admin_users=[555]
        ))
        event = Event(type="message.private", user_id=555, message="x", is_group=False)
        assert mgr.is_global_blocked(event) is False

    def test_admin_bypasses_group_whitelist(self):
        mgr = FilterManager(_make_config(
            group_mode="whitelist", group_list=[200], admin_users=[555]
        ))
        event = Event(type="message.group", user_id=555, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False

    def test_bot_self_bypasses(self):
        mgr = FilterManager(_make_config(
            group_mode="blacklist", group_list=[100], qq=123456
        ))
        event = Event(type="message.group", user_id=123456, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False

    def test_admin_bypasses_plugin_filter(self):
        mgr = FilterManager(_make_config(
            admin_users=[555],
            plugins={"echo": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            )},
        ))
        event = Event(type="message.group", user_id=555, message="x", group_id=100, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is False

    def test_bot_self_bypasses_plugin_filter(self):
        mgr = FilterManager(_make_config(
            qq=123456,
            plugins={"echo": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            )},
        ))
        event = Event(type="message.group", user_id=123456, message="x", group_id=100, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is False


# -------------------------------------------------------------------
# is_global_blocked – edge cases
# -------------------------------------------------------------------


class TestGlobalEdgeCases:
    def test_group_none_id_whitelist_blocked(self):
        mgr = FilterManager(_make_config(group_mode="whitelist", group_list=[100]))
        event = Event(type="message.group", user_id=1, message="x", group_id=None, is_group=True)
        assert mgr.is_global_blocked(event) is True

    def test_group_none_id_blacklist_passes(self):
        mgr = FilterManager(_make_config(group_mode="blacklist", group_list=[100]))
        event = Event(type="message.group", user_id=1, message="x", group_id=None, is_group=True)
        assert mgr.is_global_blocked(event) is False

    def test_no_config_passes_all(self):
        mgr = FilterManager()
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False


# -------------------------------------------------------------------
# is_plugin_blocked
# -------------------------------------------------------------------


class TestPluginBlocked:
    def test_no_config_returns_false(self):
        mgr = FilterManager()
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is False

    def test_plugin_not_configured_returns_false(self):
        mgr = FilterManager(_make_config())
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is False

    def test_plugin_disabled_returns_false(self):
        mgr = FilterManager(_make_config(
            plugins={"echo": PluginFilterConfig(enable=False)},
        ))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is False

    def test_plugin_group_blacklist_hit(self):
        mgr = FilterManager(_make_config(
            plugins={"echo": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            )},
        ))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is True

    def test_plugin_group_blacklist_miss(self):
        mgr = FilterManager(_make_config(
            plugins={"echo": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            )},
        ))
        event = Event(type="message.group", user_id=1, message="x", group_id=999, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is False

    def test_plugin_private_whitelist_hit(self):
        mgr = FilterManager(_make_config(
            plugins={"echo": PluginFilterConfig(
                enable=True,
                private=FilterModeConfig(mode="whitelist", list=[777]),
            )},
        ))
        event = Event(type="message.private", user_id=777, message="x", is_group=False)
        assert mgr.is_plugin_blocked("echo", event) is False

    def test_plugin_private_whitelist_miss(self):
        mgr = FilterManager(_make_config(
            plugins={"echo": PluginFilterConfig(
                enable=True,
                private=FilterModeConfig(mode="whitelist", list=[777]),
            )},
        ))
        event = Event(type="message.private", user_id=999, message="x", is_group=False)
        assert mgr.is_plugin_blocked("echo", event) is True

    def test_plugin_filter_off_passes(self):
        mgr = FilterManager(_make_config(
            plugins={"echo": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="off"),
            )},
        ))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is False

    def test_filter_disabled_bypasses_plugins_too(self):
        """When global filter.enable is False, plugin filters also pass."""
        mgr = FilterManager(_make_config(
            enable=False,
            plugins={"echo": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            )},
        ))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_plugin_blocked("echo", event) is False

    def test_non_message_event_passes(self):
        mgr = FilterManager(_make_config(
            plugins={"echo": PluginFilterConfig(
                enable=True,
                group=FilterModeConfig(mode="blacklist", list=[100]),
            )},
        ))
        event = Event(type="notice.notify", user_id=1, group_id=100)
        assert mgr.is_plugin_blocked("echo", event) is False


# -------------------------------------------------------------------
# update_config
# -------------------------------------------------------------------


class TestUpdateConfig:
    def test_update_config_changes_behavior(self):
        mgr = FilterManager(_make_config(group_mode="blacklist", group_list=[]))
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert mgr.is_global_blocked(event) is False  # empty blacklist

        new_cfg = _make_config(group_mode="blacklist", group_list=[100])
        mgr.update_config(new_cfg)
        assert mgr.is_global_blocked(event) is True  # now blocked
