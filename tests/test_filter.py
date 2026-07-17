"""Tests for the FilterPlugin pre-filter."""

from __future__ import annotations

import pytest

from src.core.config import FilterConfig, FilterModeConfig
from src.plugin.base import Event
from plugins.filter import FilterPlugin


# -------------------------------------------------------------------
# fixtures
# -------------------------------------------------------------------


@pytest.fixture
def plugin() -> FilterPlugin:
    return FilterPlugin()


@pytest.fixture
def bot_config_with_filter():
    """Standard bot_config extended with custom filter settings."""
    from tests.conftest import bot_config as _base_config

    # We'll use a helper to build a bot with custom filter config.
    return _base_config()


# -------------------------------------------------------------------
# helper: create a minimal bot stub for handle() tests
# -------------------------------------------------------------------


class _StubBot:
    """Minimal bot stub carrying just config for filter plugin testing."""

    def __init__(self, filter_cfg: FilterConfig, qq: int = 123456, admin_users: list[int] | None = None):
        from dataclasses import dataclass, field

        @dataclass
        class _BotSection:
            qq: int
            nickname: list = field(default_factory=list)
            admin_users: list = field(default_factory=list)

        @dataclass
        class _StubConfig:
            filter: FilterConfig
            bot: _BotSection

        self.config = _StubConfig(
            filter=filter_cfg,
            bot=_BotSection(qq=qq, admin_users=admin_users or []),
        )


def _stub_bot(**kwargs) -> _StubBot:
    return _StubBot(FilterConfig(), **kwargs)


def _stub_bot_with(
    group_mode: str = "blacklist",
    group_list: list[int] | None = None,
    private_mode: str = "blacklist",
    private_list: list[int] | None = None,
    enable: bool = True,
    qq: int = 123456,
    admin_users: list[int] | None = None,
) -> _StubBot:
    return _StubBot(
        filter_cfg=FilterConfig(
            enable=enable,
            group=FilterModeConfig(mode=group_mode, list=group_list or []),
            private=FilterModeConfig(mode=private_mode, list=private_list or []),
        ),
        qq=qq,
        admin_users=admin_users,
    )


# -------------------------------------------------------------------
# match() tests
# -------------------------------------------------------------------


class TestMatch:
    def test_match_group_message(self, plugin: FilterPlugin) -> None:
        event = Event(type="message.group", user_id=1, message="hello", group_id=100)
        assert plugin.match(event) is True

    def test_match_private_message(self, plugin: FilterPlugin) -> None:
        event = Event(type="message.private", user_id=1, message="hi")
        assert plugin.match(event) is True

    def test_match_notice_passes_through(self, plugin: FilterPlugin) -> None:
        event = Event(type="notice.notify", user_id=1, group_id=100)
        assert plugin.match(event) is False

    def test_match_request_passes_through(self, plugin: FilterPlugin) -> None:
        event = Event(type="request.friend", user_id=1)
        assert plugin.match(event) is False

    def test_match_meta_passes_through(self, plugin: FilterPlugin) -> None:
        event = Event(type="meta.heartbeat", user_id=0)
        assert plugin.match(event) is False


# -------------------------------------------------------------------
# handle() tests -- enable flag
# -------------------------------------------------------------------


class TestEnableFlag:
    @pytest.mark.asyncio
    async def test_filter_disabled_passes_all(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(enable=False)
        event = Event(type="message.group", user_id=999, message="x", group_id=1)
        assert await plugin.handle(event, bot) is False  # pass through


# -------------------------------------------------------------------
# handle() tests -- blacklist mode
# -------------------------------------------------------------------


class TestBlacklist:
    @pytest.mark.asyncio
    async def test_group_blacklist_hit_blocked(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(group_mode="blacklist", group_list=[100, 200])
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert await plugin.handle(event, bot) is True

    @pytest.mark.asyncio
    async def test_group_blacklist_miss_passes(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(group_mode="blacklist", group_list=[100])
        event = Event(type="message.group", user_id=1, message="x", group_id=999, is_group=True)
        assert await plugin.handle(event, bot) is False

    @pytest.mark.asyncio
    async def test_private_blacklist_hit_blocked(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(private_mode="blacklist", private_list=[555])
        event = Event(type="message.private", user_id=555, message="x", is_group=False)
        assert await plugin.handle(event, bot) is True

    @pytest.mark.asyncio
    async def test_private_blacklist_miss_passes(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(private_mode="blacklist", private_list=[555])
        event = Event(type="message.private", user_id=999, message="x", is_group=False)
        assert await plugin.handle(event, bot) is False

    @pytest.mark.asyncio
    async def test_empty_blacklist_passes_all(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(group_mode="blacklist", group_list=[])
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert await plugin.handle(event, bot) is False


# -------------------------------------------------------------------
# handle() tests -- whitelist mode
# -------------------------------------------------------------------


class TestWhitelist:
    @pytest.mark.asyncio
    async def test_group_whitelist_hit_passes(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(group_mode="whitelist", group_list=[100])
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert await plugin.handle(event, bot) is False

    @pytest.mark.asyncio
    async def test_group_whitelist_miss_blocked(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(group_mode="whitelist", group_list=[100])
        event = Event(type="message.group", user_id=1, message="x", group_id=999, is_group=True)
        assert await plugin.handle(event, bot) is True

    @pytest.mark.asyncio
    async def test_private_whitelist_hit_passes(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(private_mode="whitelist", private_list=[777])
        event = Event(type="message.private", user_id=777, message="x", is_group=False)
        assert await plugin.handle(event, bot) is False

    @pytest.mark.asyncio
    async def test_private_whitelist_miss_blocked(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(private_mode="whitelist", private_list=[777])
        event = Event(type="message.private", user_id=999, message="x", is_group=False)
        assert await plugin.handle(event, bot) is True

    @pytest.mark.asyncio
    async def test_empty_whitelist_blocks_all(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(group_mode="whitelist", group_list=[])
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert await plugin.handle(event, bot) is True


# -------------------------------------------------------------------
# handle() tests -- mode="off"
# -------------------------------------------------------------------


class TestModeOff:
    @pytest.mark.asyncio
    async def test_group_off_passes_all(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(group_mode="off")
        event = Event(type="message.group", user_id=1, message="x", group_id=100, is_group=True)
        assert await plugin.handle(event, bot) is False

    @pytest.mark.asyncio
    async def test_private_off_passes_all(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(private_mode="off")
        event = Event(type="message.private", user_id=1, message="x", is_group=False)
        assert await plugin.handle(event, bot) is False


# -------------------------------------------------------------------
# handle() tests -- admin / bot self bypass
# -------------------------------------------------------------------


class TestBypass:
    @pytest.mark.asyncio
    async def test_admin_bypasses_group_blacklist(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(
            group_mode="blacklist", group_list=[100], admin_users=[555]
        )
        event = Event(type="message.group", user_id=555, message="x", group_id=100, is_group=True)
        assert await plugin.handle(event, bot) is False

    @pytest.mark.asyncio
    async def test_admin_bypasses_private_blacklist(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(
            private_mode="blacklist", private_list=[555], admin_users=[555]
        )
        event = Event(type="message.private", user_id=555, message="x", is_group=False)
        assert await plugin.handle(event, bot) is False

    @pytest.mark.asyncio
    async def test_admin_bypasses_group_whitelist(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(
            group_mode="whitelist", group_list=[200], admin_users=[555]
        )
        event = Event(type="message.group", user_id=555, message="x", group_id=100, is_group=True)
        assert await plugin.handle(event, bot) is False

    @pytest.mark.asyncio
    async def test_bot_self_bypasses(self, plugin: FilterPlugin) -> None:
        bot = _stub_bot_with(
            group_mode="blacklist", group_list=[100], qq=123456
        )
        event = Event(type="message.group", user_id=123456, message="x", group_id=100, is_group=True)
        assert await plugin.handle(event, bot) is False


# -------------------------------------------------------------------
# handle() tests -- group_id=None edge case
# -------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_group_none_id_whitelist(self, plugin: FilterPlugin) -> None:
        """None group_id with whitelist: None not in list → blocked."""
        bot = _stub_bot_with(group_mode="whitelist", group_list=[100])
        event = Event(type="message.group", user_id=1, message="x", group_id=None, is_group=True)
        assert await plugin.handle(event, bot) is True

    @pytest.mark.asyncio
    async def test_group_none_id_blacklist(self, plugin: FilterPlugin) -> None:
        """None group_id with blacklist: None not in list → passes."""
        bot = _stub_bot_with(group_mode="blacklist", group_list=[100])
        event = Event(type="message.group", user_id=1, message="x", group_id=None, is_group=True)
        assert await plugin.handle(event, bot) is False
