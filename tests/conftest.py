"""Shared test fixtures for the entire test suite."""

from __future__ import annotations

from typing import Any

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
from src.plugin.manager import PluginManager

# -------------------------------------------------------------------
# config fixtures
# -------------------------------------------------------------------


@pytest.fixture
def bot_config() -> BotConfig:
    """Minimal valid BotConfig for testing."""
    return BotConfig(
        bot=BotConfigSection(qq=123456, nickname=["test"], admin_users=[111]),
        napcat=NapCatConfig(ws_url="ws://127.0.0.1:3001"),
        plugin=PluginConfig(auto_discover=False),
        logging=LoggingConfigSection(level="DEBUG"),
        env={},
    )


# -------------------------------------------------------------------
# API client fixtures
# -------------------------------------------------------------------


class _FakeApiClient:
    """In-memory ApiClient test double that records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._client: Any = None
        self._fail_on: str | None = None
        self._raise_error: Exception | None = None

    # lifecycle
    def set_client(self, client: Any) -> None:
        self._client = client

    def clear_client(self) -> None:
        self._client = None

    async def send_group_msg(self, group_id: int, message: str) -> dict[str, Any]:
        if self._fail_on == "send_group_msg":
            raise self._raise_error or RuntimeError("send_group_msg failed")
        self.calls.append({"method": "send_group_msg", "group_id": group_id, "message": message})
        return {"status": "ok"}

    async def send_private_msg(self, user_id: int, message: str) -> dict[str, Any]:
        self.calls.append({"method": "send_private_msg", "user_id": user_id, "message": message})
        return {"status": "ok"}

    async def get_group_info(self, group_id: int) -> dict[str, Any]:
        self.calls.append({"method": "get_group_info", "group_id": group_id})
        return {}

    async def get_group_member_info(self, group_id: int, user_id: int) -> dict[str, Any]:
        self.calls.append({"method": "get_group_member_info", "group_id": group_id, "user_id": user_id})
        return {}

    async def get_group_member_list(self, group_id: int) -> dict[str, Any]:
        self.calls.append({"method": "get_group_member_list", "group_id": group_id})
        return {}

    async def set_group_ban(self, group_id: int, user_id: int, duration: int) -> dict[str, Any]:
        self.calls.append({"method": "set_group_ban", "group_id": group_id, "user_id": user_id, "duration": duration})
        return {}

    async def set_group_kick(self, group_id: int, user_id: int) -> dict[str, Any]:
        self.calls.append({"method": "set_group_kick", "group_id": group_id, "user_id": user_id})
        return {}

    async def get_login_info(self) -> dict[str, Any]:
        self.calls.append({"method": "get_login_info"})
        return {}

    async def get_friend_list(self) -> dict[str, Any]:
        self.calls.append({"method": "get_friend_list"})
        return {}

    async def get_stranger_info(self, user_id: int) -> dict[str, Any]:
        self.calls.append({"method": "get_stranger_info", "user_id": user_id})
        return {}

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"method": "call_action", "action": action, "params": params})
        return {}


@pytest.fixture
def mock_api_client() -> _FakeApiClient:
    """A fake ApiClient that records all calls for assertions."""
    return _FakeApiClient()


# -------------------------------------------------------------------
# bot fixture
# -------------------------------------------------------------------


@pytest.fixture
def bot(bot_config: BotConfig, mock_api_client: _FakeApiClient) -> Bot:
    """Bot instance with injected config and fake API client."""
    b = Bot(config=bot_config)
    b.api = mock_api_client  # type: ignore[assignment]
    return b


# -------------------------------------------------------------------
# event fixtures
# -------------------------------------------------------------------


@pytest.fixture
def event_group() -> Event:
    """A standard group message event."""
    return Event(
        type="message.group",
        user_id=123,
        message="/ping",
        group_id=456,
        is_group=True,
    )


@pytest.fixture
def event_private() -> Event:
    """A standard private message event."""
    return Event(
        type="message.private",
        user_id=789,
        message="hello",
        is_group=False,
    )


# -------------------------------------------------------------------
# plugin manager fixture
# -------------------------------------------------------------------


@pytest.fixture
def plugin_manager() -> PluginManager:
    """An empty PluginManager."""
    return PluginManager()
