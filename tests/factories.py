"""Test data factories for creating test doubles quickly."""

from __future__ import annotations

from src.core.llm import LlmResponse, ToolCall
from src.core.message_bus import BusMessage, MessageType
from src.plugin.base import Event


def make_event(
    *,
    type: str = "message.group",
    user_id: int = 123,
    message: str = "",
    group_id: int | None = 456,
    is_group: bool = True,
    message_id: int | None = 1,
    raw: dict | None = None,
) -> Event:
    """Create an Event with sensible defaults."""
    return Event(
        type=type,
        user_id=user_id,
        message=message,
        group_id=group_id,
        is_group=is_group,
        message_id=message_id,
        raw=raw,
    )


def make_bus_message(
    type: MessageType = MessageType.EXTERNAL,
    payload: object = None,
    source: str = "test",
    depth: int = 0,
) -> BusMessage:
    """Create a BusMessage with sensible defaults."""
    return BusMessage(type=type, payload=payload or {}, source=source, depth=depth)


def make_llm_response_text(text: str = "hello") -> LlmResponse:
    """Create an LlmResponse with text content."""
    return LlmResponse(text=text)


def make_llm_response_tool(calls: list[ToolCall] | None = None) -> LlmResponse:
    """Create an LlmResponse with tool calls."""
    if calls is None:
        calls = [ToolCall(id="call_00", name="get_time", arguments={})]
    return LlmResponse(tool_calls=calls)
