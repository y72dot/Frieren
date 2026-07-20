"""Shared test fixtures for the entire test suite."""

from __future__ import annotations

from typing import Any

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
from src.core.llm import LlmResponse
from src.core.message_bus import MessageBus
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
# bus fixture
# -------------------------------------------------------------------


@pytest.fixture
def bus() -> MessageBus:
    """A fresh MessageBus for testing."""
    return MessageBus()


# -------------------------------------------------------------------
# API client fixtures
# -------------------------------------------------------------------


class _FakeApiClient:
    """In-memory ApiClient test double that records calls.

    When a bus is injected, ACTION methods route through it.  Without
    a bus (the default), calls are recorded directly for backward
    compatibility with existing tests.
    """

    def __init__(self, bus: MessageBus | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._client: Any = None
        self._bus = bus
        self._fail_on: str | None = None
        self._raise_error: Exception | None = None

    # lifecycle
    def set_client(self, client: Any) -> None:
        self._client = client

    def clear_client(self) -> None:
        self._client = None

    async def _raw_call(self, action: str, **params: Any) -> dict[str, Any]:
        """Direct call bypassing the bus (used by _qq_exec)."""
        self.calls.append({"method": action, **params})
        return {"status": "ok"}

    async def send_group_msg(self, group_id: int, message: str) -> dict[str, Any]:
        if self._bus is not None:
            from src.core.message_bus import BusMessage, MessageType

            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": group_id,
                    "message": message,
                },
                source="test",
            )
            result = await self._bus.dispatch(msg, None)
            return result if isinstance(result, dict) else {}
        if self._fail_on == "send_group_msg":
            raise self._raise_error or RuntimeError("send_group_msg failed")
        self.calls.append(
            {"method": "send_group_msg", "group_id": group_id, "message": message}
        )
        return {"status": "ok"}

    async def send_private_msg(self, user_id: int, message: str) -> dict[str, Any]:
        if self._bus is not None:
            from src.core.message_bus import BusMessage, MessageType

            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_private_msg",
                    "user_id": user_id,
                    "message": message,
                },
                source="test",
            )
            result = await self._bus.dispatch(msg, None)
            return result if isinstance(result, dict) else {}
        self.calls.append(
            {"method": "send_private_msg", "user_id": user_id, "message": message}
        )
        return {"status": "ok"}

    async def get_group_info(self, group_id: int) -> dict[str, Any]:
        self.calls.append({"method": "get_group_info", "group_id": group_id})
        return {}

    async def get_group_member_info(
        self, group_id: int, user_id: int
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "get_group_member_info",
                "group_id": group_id,
                "user_id": user_id,
            }
        )
        return {}

    async def get_group_member_list(self, group_id: int) -> dict[str, Any]:
        self.calls.append({"method": "get_group_member_list", "group_id": group_id})
        return {}

    async def set_group_ban(
        self, group_id: int, user_id: int, duration: int
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "set_group_ban",
                "group_id": group_id,
                "user_id": user_id,
                "duration": duration,
            }
        )
        return {}

    async def set_group_kick(self, group_id: int, user_id: int) -> dict[str, Any]:
        self.calls.append(
            {"method": "set_group_kick", "group_id": group_id, "user_id": user_id}
        )
        return {}

    async def send_group_poke(self, group_id: int, user_id: int) -> dict[str, Any]:
        self.calls.append(
            {"method": "send_group_poke", "group_id": group_id, "user_id": user_id}
        )
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

    async def send_group_forward_msg(
        self, group_id: int, nodes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.calls.append(
            {"method": "send_group_forward_msg", "group_id": group_id, "nodes": nodes}
        )
        return {}

    async def get_msg(self, message_id: int) -> dict[str, Any]:
        self.calls.append({"method": "get_msg", "message_id": message_id})
        return {}

    async def set_essence_msg(self, message_id: int) -> dict[str, Any]:
        self.calls.append({"method": "set_essence_msg", "message_id": message_id})
        return {"status": "ok"}

    async def delete_essence_msg(self, message_id: int) -> dict[str, Any]:
        self.calls.append({"method": "delete_essence_msg", "message_id": message_id})
        return {"status": "ok"}

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
    from src.core.message_store import MessageStore

    b = Bot(config=bot_config)
    b.api = mock_api_client  # type: ignore[assignment]
    b.msg_store = MessageStore(db_path=":memory:")
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
def plugin_manager(bus: MessageBus) -> PluginManager:
    """An empty PluginManager backed by a fresh MessageBus."""
    return PluginManager(bus=bus)


# -------------------------------------------------------------------
# LLM fixtures
# -------------------------------------------------------------------


class FakeLlmProvider:
    """Configurable fake LLM provider for testing.

    Set ``responses`` to a list of :class:`LlmResponse` objects to
    control the sequence of replies returned by ``chat_completion()``.
    """

    def __init__(self) -> None:
        self.responses: list[LlmResponse] = []
        self.calls: list[dict] = []
        self._cursor = 0

    async def chat_completion(
        self, messages, *, tools=None, **kwargs
    ) -> LlmResponse:
        self.calls.append(
            {"messages": messages, "tools": tools, **kwargs}
        )
        if self._cursor < len(self.responses):
            resp = self.responses[self._cursor]
            self._cursor += 1
            return resp
        # Default: empty text response
        return LlmResponse(text="fake reply")

    def reset(self) -> None:
        """Reset call history and cursor (but keep responses)."""
        self.calls.clear()
        self._cursor = 0


@pytest.fixture
def fake_llm() -> FakeLlmProvider:
    """A fresh FakeLlmProvider."""
    return FakeLlmProvider()


@pytest.fixture
def bot_with_llm(bot_config: BotConfig) -> Bot:
    """Bot instance with LLM enabled and a fake provider."""
    from src.core.message_store import MessageStore

    bot_config.llm = LLMConfig(
        enabled=True,
        api_base="https://fake-api.example.com/v1",
        api_key="sk-fake",
        model="fake-model",
        max_tokens=512,
        temperature=0.0,
        max_turns=3,
    )
    b = Bot(config=bot_config)
    b.api = _FakeApiClient()
    b.llm_provider = FakeLlmProvider()
    b.msg_store = MessageStore(db_path=":memory:")
    return b
