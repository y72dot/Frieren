"""LLM core plugin: main LLM agent loop and API call orchestration."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.core.llm import SessionManager, ToolCall
from src.core.message_bus import BusMessage, MessageType
from src.plugin.decorators import subscribe

# ---------------------------------------------------------------------------
# Module-level shared state (accessed by llm_memory / llm_tools)
# ---------------------------------------------------------------------------

_session_mgr: SessionManager | None = None
_tools_registry: list[dict] = []


def _lazy_init(bot) -> None:
    """Lazily initialize the shared SessionManager and tool registry."""
    global _session_mgr, _tools_registry
    if _session_mgr is not None:
        return

    _session_mgr = SessionManager(max_messages=bot.config.llm.max_turns * 4 + 10)

    # Collect tool definitions from llm_tools module
    try:
        from plugins.llm_tools import TOOL_DEFS

        _tools_registry = TOOL_DEFS
        logger.debug(f"LLM tools registered: {len(_tools_registry)} tool(s)")
    except ImportError:
        logger.warning("llm_tools module not found, no tools registered")
        _tools_registry = []


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


@subscribe(MessageType.INTERNAL, priority=50)
async def llm_core_handler(payload: dict[str, Any], bot) -> bool:
    """Handle ``llm_type: "trigger"`` INTERNAL messages – run the LLM agent loop."""
    if payload.get("llm_type") != "trigger":
        return False

    _lazy_init(bot)
    assert _session_mgr is not None

    session_key: str = payload["session_key"]
    text: str = payload["text"]
    nickname: str = payload.get("nickname", "")
    is_group: bool = payload["is_group"]
    cfg = bot.config.llm

    # Add user message to session
    user_content = f"{nickname}: {text}" if is_group else text
    await _session_mgr.add_message(session_key, "user", user_content)

    # 1. Notify memory plugin to inject context
    await bot.message_bus.emit_and_wait(
        BusMessage(
            type=MessageType.INTERNAL,
            payload={
                "llm_type": "context",
                "session_key": session_key,
                "is_group": is_group,
            },
            source="llm_core",
        ),
        bot,
    )

    # 2. Build messages for LLM
    messages: list[dict] = [{"role": "system", "content": cfg.system_prompt}]
    messages.extend(await _session_mgr.get_messages(session_key))

    # 3. Multi-turn LLM loop
    max_turns = cfg.max_turns
    for turn in range(1, max_turns + 1):
        logger.debug(f"LLM turn {turn}/{max_turns} for [{session_key}]")

        response = await bot.llm_provider.chat_completion(
            messages,
            tools=_tools_registry if _tools_registry else None,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
        )

        if not response.tool_calls:
            # Plain text reply – send it and finish
            reply = response.text or ""
            if reply.strip():
                await _session_mgr.add_message(session_key, "assistant", reply)
                await bot.message_bus.emit_and_wait(
                    BusMessage(
                        type=MessageType.INTERNAL,
                        payload={
                            "llm_type": "send",
                            "target_id": payload.get("group_id") or payload["user_id"],
                            "is_group": is_group,
                            "text": reply,
                        },
                        source="llm_core",
                    ),
                    bot,
                )
            break

        # Tool calls – execute and continue
        messages.append(_make_assistant_tool_msg(response.tool_calls))

        response_buf: dict[str, Any] = {}
        await bot.message_bus.emit_and_wait(
            BusMessage(
                type=MessageType.INTERNAL,
                payload={
                    "llm_type": "tool",
                    "session_key": session_key,
                    "tool_calls": response.tool_calls,
                    "response_buffer": response_buf,
                    "is_group": is_group,
                    "group_id": payload.get("group_id"),
                    "user_id": payload["user_id"],
                },
                source="llm_core",
            ),
            bot,
        )

        # Append tool results to messages
        for result in response_buf.get("results", []):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result["call_id"],
                    "content": json.dumps(result["result"], ensure_ascii=False),
                }
            )
    else:
        # Reached max_turns – force final completion without tools
        logger.warning(f"LLM agent reached max_turns for [{session_key}], forcing final reply")
        response = await bot.llm_provider.chat_completion(
            messages,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
        )
        reply = response.text or ""
        if reply.strip():
            await _session_mgr.add_message(session_key, "assistant", reply)
            await bot.message_bus.emit_and_wait(
                BusMessage(
                    type=MessageType.INTERNAL,
                    payload={
                        "llm_type": "send",
                        "target_id": payload.get("group_id") or payload["user_id"],
                        "is_group": is_group,
                        "text": reply,
                    },
                    source="llm_core",
                ),
                bot,
            )

    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assistant_tool_msg(tool_calls: list[ToolCall]) -> dict:
    """Convert ToolCall list to an OpenAI-format assistant message with tool_calls."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ],
    }
