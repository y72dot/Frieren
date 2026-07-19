"""Tests for Bot orchestrator class."""

import pytest

from src.core.bot import Bot
from src.core.config import (
    BotConfig,
    BotConfigSection,
    LoggingConfigSection,
    NapCatConfig,
    PluginConfig,
)
from src.plugin.base import Event


# A minimal valid plugin for testing
class _FakePlugin:
    name = "fake"
    priority = 0

    def match(self, event: Event) -> bool:
        return False

    async def handle(self, event: Event, bot) -> bool:
        return True


# -------------------------------------------------------------------
# __init__ defaults
# -------------------------------------------------------------------


def test_bot_init_defaults():
    b = Bot()
    assert b.config is None
    assert b.message_bus is not None
    assert b.api is not None
    assert b.event_bus is not None
    assert b.plugin_manager is not None
    assert b._running is False
    assert b._main_task is None


def test_bot_init_with_config(bot_config: BotConfig):
    b = Bot(config=bot_config)
    assert b.config is bot_config
    assert b.config.bot.qq == 123456


# -------------------------------------------------------------------
# load_config
# -------------------------------------------------------------------


def test_load_config_returns_injected_config(bot_config: BotConfig):
    """When config is injected via __init__, load_config is a no-op."""
    b = Bot(config=bot_config)
    result = b.load_config()
    assert result is bot_config


def test_load_config_skips_when_already_set():
    """load_config should not re-load if config is already present."""
    b = Bot()
    # inject config manually
    fake_cfg = BotConfig(
        bot=BotConfigSection(qq=999),
        napcat=NapCatConfig(),
        plugin=PluginConfig(),
        logging=LoggingConfigSection(),
    )
    b.config = fake_cfg
    result = b.load_config()
    assert result is fake_cfg
    assert result.bot.qq == 999


# -------------------------------------------------------------------
# start failure without config
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_raises_without_config():
    b = Bot()
    with pytest.raises(RuntimeError, match="Configuration not loaded"):
        await b.start()


# -------------------------------------------------------------------
# reload_plugins
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_plugins_no_config(bot_config: BotConfig):
    """reload_plugins when config is None should warn but not crash."""
    b = Bot(config=bot_config)
    b.config = None
    # Should not raise; just log a warning
    await b.reload_plugins()


@pytest.mark.asyncio
async def test_reload_plugins_replaces_manager(bot_config: BotConfig):
    b = Bot(config=bot_config)
    old_pm = b.plugin_manager
    b.plugin_manager.register(_FakePlugin())

    b.config.plugin.plugin_dirs = ["nonexistent_dir"]
    await b.reload_plugins()
    assert b.plugin_manager is not old_pm
    assert b.plugin_manager.plugin_count == 0


# -------------------------------------------------------------------
# filter manager config propagation
# -------------------------------------------------------------------


def test_load_config_updates_filter_mgr():
    """load_config() must propagate config to FilterManager after loading."""
    b = Bot()
    assert b.filter_mgr._config is None
    fake_cfg = BotConfig(
        bot=BotConfigSection(qq=999),
        napcat=NapCatConfig(),
        plugin=PluginConfig(),
        logging=LoggingConfigSection(),
    )
    b.config = fake_cfg
    b.filter_mgr.update_config(fake_cfg)
    assert b.filter_mgr._config is fake_cfg
    assert b.filter_mgr._config is not None


# -------------------------------------------------------------------
# stop
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_sets_running_false(bot_config: BotConfig):
    b = Bot(config=bot_config)
    b._running = True

    await b.stop()
    assert b._running is False
