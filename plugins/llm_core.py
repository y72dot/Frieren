"""LLM core plugin: main LLM agent loop and API call orchestration."""

from __future__ import annotations

import json
import time as _time
from typing import Any

from loguru import logger

from src.core.llm import LlmSessionLogger, ToolCall
from src.core.message_bus import BusMessage, MessageType
from src.plugin.decorators import subscribe

# ---------------------------------------------------------------------------
# Module-level shared state
# ---------------------------------------------------------------------------

_tools_registry: list[dict] = []
# {session_key: (last_active_timestamp, messages)}
_session_cache: dict[str, tuple[float, list[dict]]] = {}


def _lazy_init(bot) -> None:
    """Lazily initialize the tool registry."""
    global _tools_registry
    if _tools_registry:
        return

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

    text: str = payload["text"]
    nickname: str = payload.get("nickname", "")
    is_group: bool = payload["is_group"]
    session_key: str = payload["session_key"]
    session_log = LlmSessionLogger(session_key)
    session_log.trigger(nickname, text, is_group)
    cfg = bot.config.llm

    # Build user content
    user_content = f"{nickname}: {text}" if is_group else text

    # Reuse or create session based on TTL
    ttl = cfg.session_ttl
    now = _time.time()
    entry = _session_cache.get(session_key)
    if entry is not None and ttl > 0 and now - entry[0] < ttl:
        # Reuse existing session: append new user message
        messages = entry[1]
        messages.append({"role": "user", "content": user_content})
        logger.debug(f"Session [{session_key}] reused, {len(messages)} messages")
        session_log.session_reuse(len(messages), now - entry[0])
    else:
        messages = _new_session(cfg.system_prompt, user_content)
        session_log.session_new(len(messages))

    # Touch timestamp at start
    _session_cache[session_key] = (now, messages)

    # Multi-turn LLM loop
    max_turns = cfg.max_turns
    for turn in range(1, max_turns + 1):
        logger.debug(f"LLM turn {turn}/{max_turns}")
        session_log.turn_start(turn, max_turns)
        session_log.request(messages, cfg.model, len(_tools_registry))

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
            session_log.text_response(reply)
            if reply.strip():
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
            if reply.strip():
                session_log.final_text(reply)
            break

        # Tool calls – execute and continue
        session_log.tool_calls_result(response.tool_calls)
        assistant_tool_msg = _make_assistant_tool_msg(response.tool_calls)
        messages.append(assistant_tool_msg)

        response_buf: dict[str, Any] = {}
        await bot.message_bus.emit_and_wait(
            BusMessage(
                type=MessageType.INTERNAL,
                payload={
                    "llm_type": "tool",
                    "session_key": payload["session_key"],
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

        # Append tool results to local messages
        for result in response_buf.get("results", []):
            session_log.tool_result(
                result["call_id"],
                result.get("name", "?"),
                json.dumps(result["result"], ensure_ascii=False),
            )
            tool_msg = {
                "role": "tool",
                "tool_call_id": result["call_id"],
                "content": json.dumps(result["result"], ensure_ascii=False),
            }
            messages.append(tool_msg)
    else:
        # Reached max_turns – force final completion without tools
        logger.warning("LLM agent reached max_turns, forcing final reply")
        session_log.max_turns_forced()
        response = await bot.llm_provider.chat_completion(
            messages,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
        )
        reply = response.text or ""
        session_log.text_response(reply)
        if reply.strip():
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
            session_log.final_text(reply)

    # Update session cache timestamp after loop
    _session_cache[session_key] = (_time.time(), messages)
    session_log.session_end(turn)

    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_session(system_prompt: str, user_content: str) -> list[dict]:
    """Create a fresh messages list for a new session."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


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
