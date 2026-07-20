"""Tests for the bot entry point (src/main.py)."""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest


class TestMain:
    def test_main_loads_config_and_starts(self):
        with mock.patch("src.main.Bot") as bot_cls:
            mock_bot = bot_cls.return_value
            mock_bot.start = mock.AsyncMock()

            from src.main import main

            asyncio.run(main())

            mock_bot.load_config.assert_called_once_with(config_dir=None)
            mock_bot.start.assert_awaited_once()

    def test_main_with_config_dir_env(self, monkeypatch):
        monkeypatch.setenv("BOT_CONFIG_DIR", "/custom/config")

        with mock.patch("src.main.Bot") as bot_cls:
            mock_bot = bot_cls.return_value
            mock_bot.start = mock.AsyncMock()

            from src.main import main

            asyncio.run(main())

            mock_bot.load_config.assert_called_once_with(config_dir="/custom/config")
            mock_bot.start.assert_awaited_once()

    def test_main_propagates_error(self):
        with mock.patch("src.main.Bot") as bot_cls:
            mock_bot = bot_cls.return_value
            mock_bot.start = mock.AsyncMock(side_effect=RuntimeError("startup failed"))

            from src.main import main

            with pytest.raises(RuntimeError, match="startup failed"):
                asyncio.run(main())
