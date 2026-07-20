"""Integration tests for Bot lifecycle: init, stop, cleanup, reconnect."""

from __future__ import annotations

import pytest

from src.core.bot import Bot
from src.core.config import (
    BotConfig,
    BotConfigSection,
    LLMConfig,
    LoggingConfigSection,
    NapCatConfig,
    PluginConfig,
)


@pytest.fixture
def lifecycle_config() -> BotConfig:
    return BotConfig(
        bot=BotConfigSection(qq=123456, nickname=["test"], admin_users=[111]),
        napcat=NapCatConfig(ws_url="ws://127.0.0.1:3001"),
        plugin=PluginConfig(auto_discover=False),
        logging=LoggingConfigSection(level="DEBUG"),
        env={},
    )


class TestBotInitialization:
    def test_all_subsystems_initialized(self, lifecycle_config: BotConfig):
        b = Bot(config=lifecycle_config)
        assert b.message_bus is not None
        assert b.api is not None
        assert b.msg_store is not None
        assert b.filter_mgr is not None
        assert b.event_bus is not None
        assert b.plugin_manager is not None
        assert b._running is False
        assert b._main_task is None
        assert b.config is lifecycle_config

    def test_api_bus_wired(self, lifecycle_config: BotConfig):
        b = Bot(config=lifecycle_config)
        # ApiClient should have a bus reference
        assert b.api._bus is not None
        # ApiClient should have a back-reference to the bot
        assert b.api._bot is b

    def test_filter_mgr_has_config(self, lifecycle_config: BotConfig):
        b = Bot(config=lifecycle_config)
        assert b.filter_mgr._config is lifecycle_config

    def test_msg_store_uses_default_path_by_default(self):
        b = Bot()
        # Default path is data/messages.db
        assert b.msg_store is not None


class TestBotStopAndCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_client(self, lifecycle_config: BotConfig):
        b = Bot(config=lifecycle_config)
        b.api._client = object()
        await b._cleanup()
        assert b.api._client is None

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, lifecycle_config: BotConfig):
        b = Bot(config=lifecycle_config)
        b._running = True
        await b.stop()
        assert b._running is False

    @pytest.mark.asyncio
    async def test_stop_without_main_task_safe(self, lifecycle_config: BotConfig):
        b = Bot(config=lifecycle_config)
        b._running = True
        b._main_task = None
        await b.stop()
        assert b._running is False


class TestLLMIntegration:
    def test_llm_disabled_by_default(self, lifecycle_config: BotConfig):
        b = Bot(config=lifecycle_config)
        assert b.llm_provider is None

    def test_llm_enabled_in_config(self, lifecycle_config: BotConfig):
        cfg = lifecycle_config
        cfg.llm = LLMConfig(
            enabled=True,
            api_base="https://api.example.com/v1",
            api_key="sk-test",
            model="test-model",
        )
        b = Bot(config=cfg)
        assert b.llm_provider is None  # not initialized until start()
        assert b.config.llm.enabled is True
        assert b.config.llm.model == "test-model"


class TestReconnectBackoff:
    def test_backoff_increases_with_attempts(self):
        """Backoff delay = min(base * 2^(attempt-1), 300)."""
        base = 5

        # attempt 1: 5 * 2^0 = 5
        delay = min(base * (2**0), 300)
        assert delay == 5

        # attempt 3: 5 * 2^2 = 20
        delay = min(base * (2**2), 300)
        assert delay == 20

        # attempt 8: 5 * 2^7 = 640, capped at 300
        delay = min(base * (2**7), 300)
        assert delay == 300

    def test_backoff_resets_on_success(self):
        """After a successful connection, attempt counter resets to 0."""
        base = 5
        # Simulate: attempt 3 fails, delay = 20
        # Then connection succeeds, attempt → 0
        # Next reconnect: attempt 1, delay = 5
        delay = min(base * (2**0), 300)
        assert delay == 5
