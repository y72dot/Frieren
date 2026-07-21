"""LLM core plugin: main LLM agent loop and API call orchestration."""

from __future__ import annotations

import json
import time as _time
from typing import Any

from loguru import logger

from src.core.llm import LlmSessionLogger
from src.core.llm.agent_loop import AgentLoop, AgentResult, LoopConfig
from src.core.llm.circuit_breaker import CircuitBreaker
from src.core.llm.session_manager import Session, SessionManager
from src.core.llm.tool_permissions import ToolCallContext
from src.core.message_bus import BusMessage, MessageType
from src.plugin.decorators import subscribe

# ---------------------------------------------------------------------------
# Module-level shared state (kept for backward compatibility with tests)
# ---------------------------------------------------------------------------

_tools_registry: list[dict] = []
# {session_key: (last_active_timestamp, messages)}
_session_cache: dict[str, tuple[float, list[dict]]] = {}


def _lazy_init(bot) -> None:
    """Lazily initialize the tool registry and agent subsystems on the bot."""
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

    # Initialise agent subsystems on bot if not already done
    if not hasattr(bot, "_agent_initialized"):
        _init_bot_agent(bot)
        bot._agent_initialized = True


def _init_bot_agent(bot) -> None:
    """Create and attach AgentLoop + SessionManager to the bot."""
    from plugins.llm_tools import _catalog, _executor

    cfg = bot.config.llm

    # Session manager (persistence + pruning)
    session_mgr = SessionManager(
        ttl=cfg.session_ttl,
    )
    session_mgr.init_db()
    session_mgr.recover()
    bot.session_mgr = session_mgr

    # Note: _session_cache stays as its own dict for backward compat.
    # When session_mgr is available, it's used as the primary store;
    # _session_cache is only used as a fallback when session_mgr is None.

    # Agent loop
    bot.agent_loop = AgentLoop(
        catalog=_catalog,
        session_mgr=session_mgr,
        executor=_executor,
        breaker=CircuitBreaker(),
        config=LoopConfig(max_turns=cfg.max_turns),
    )


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

    # Reuse or create session
    session_mgr: SessionManager = getattr(bot, "session_mgr", None)
    if session_mgr is not None:
        session = session_mgr.get_or_create(session_key, cfg.system_prompt, user_content)
        is_new = session.turn_count == 0
        if is_new:
            logger.info(f"LLM session start: key={session_key} nickname={nickname} text={text[:80]}")
            _inject_recent_history(session.messages, session_key, bot, payload)
            session_log.session_new(len(session.messages))
        else:
            logger.info(f"LLM session reuse: key={session_key} nickname={nickname} text={text[:80]}")
            session_log.session_reuse(len(session.messages), _time.time() - session.last_active)
    else:
        # Fallback: use module-level cache directly (test backward compat)
        ttl = cfg.session_ttl
        now = _time.time()
        entry = _session_cache.get(session_key)
        if entry is not None and ttl > 0 and now - entry[0] < ttl:
            messages = entry[1]
            messages.append({"role": "user", "content": user_content})
            logger.info(f"LLM session reuse: key={session_key} nickname={nickname} text={text[:80]}")
            session_log.session_reuse(len(messages), now - entry[0])
        else:
            messages = _new_session(cfg.system_prompt, user_content)
            logger.info(f"LLM session start: key={session_key} nickname={nickname} text={text[:80]}")
            _inject_recent_history(messages, session_key, bot, payload)
            session_log.session_new(len(messages))
        _session_cache[session_key] = (now, messages)
        session = Session(
            session_key=session_key,
            messages=messages,
            last_active=now,
        )

    # Build tool call context
    ctx = ToolCallContext(
        user_id=payload["user_id"],
        group_id=payload.get("group_id") if is_group else None,
        user_is_admin=payload["user_id"] in bot.config.bot.admin_users,
    )

    # Run the agent loop
    agent_loop: AgentLoop | None = getattr(bot, "agent_loop", None)
    if agent_loop is not None:
        result = await agent_loop.run(session, ctx, bot)
    else:
        # Fallback: inline loop for tests without agent_loop
        result = await _inline_loop(session, ctx, bot, payload, session_log)

    # Post-loop: update session state
    session.turn_count += result.turns
    session.last_active = _time.time()
    if session_mgr is not None:
        session_mgr.save(session)
    else:
        _session_cache[session_key] = (session.last_active, session.messages)

    logger.info(f"LLM session end: key={session_key} turns={result.turns}")
    session_log.session_end(result.turns)

    return False


# ---------------------------------------------------------------------------
# Fallback inline loop (for tests without AgentLoop on bot)
# ---------------------------------------------------------------------------


async def _inline_loop(
    session: Session,
    ctx: ToolCallContext,
    bot,
    trigger_payload: dict,
    session_log: LlmSessionLogger,
) -> AgentResult:
    """Inline agent loop fallback when bot.agent_loop is not set up."""
    cfg = bot.config.llm
    max_turns = cfg.max_turns
    turn = 0

    for turn in range(1, max_turns + 1):
        logger.debug(f"LLM turn {turn}/{max_turns}")
        session_log.turn_start(turn, max_turns)
        session_log.request(session.messages, cfg.model, len(_tools_registry))

        response = await bot.llm_provider.chat_completion(
            session.messages,
            tools=_tools_registry if _tools_registry else None,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
        )

        if not response.tool_calls:
            reply = response.text or ""
            session_log.text_response(reply)
            if reply.strip():
                await bot.message_bus.emit_and_wait(
                    BusMessage(
                        type=MessageType.INTERNAL,
                        payload={
                            "llm_type": "send",
                            "target_id": trigger_payload.get("group_id") or trigger_payload["user_id"],
                            "is_group": ctx.group_id is not None,
                            "text": reply,
                        },
                        source="llm_core",
                    ),
                    bot,
                )
                session_log.final_text(reply)
            logger.info(f"LLM final reply: session={session.session_key} len={len(reply)} chars")
            return AgentResult(final_text=reply, turns=turn)

        session_log.tool_calls_result(response.tool_calls)
        session.messages.append(_make_assistant_tool_msg(response.tool_calls))

        response_buf: dict[str, Any] = {}
        await bot.message_bus.emit_and_wait(
            BusMessage(
                type=MessageType.INTERNAL,
                payload={
                    "llm_type": "tool",
                    "session_key": session.session_key,
                    "tool_calls": response.tool_calls,
                    "response_buffer": response_buf,
                    "is_group": ctx.group_id is not None,
                    "group_id": ctx.group_id,
                    "user_id": trigger_payload["user_id"],
                },
                source="llm_core",
            ),
            bot,
        )

        for result in response_buf.get("results", []):
            session_log.tool_result(
                result["call_id"],
                result.get("name", "?"),
                json.dumps(result["result"], ensure_ascii=False),
            )
            session.messages.append({
                "role": "tool",
                "tool_call_id": result["call_id"],
                "content": json.dumps(result["result"], ensure_ascii=False),
            })
    else:
        logger.warning("LLM agent reached max_turns, forcing final reply")
        session_log.max_turns_forced()
        response = await bot.llm_provider.chat_completion(
            session.messages,
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
                        "target_id": trigger_payload.get("group_id") or trigger_payload["user_id"],
                        "is_group": ctx.group_id is not None,
                        "text": reply,
                    },
                    source="llm_core",
                ),
                bot,
            )
            session_log.final_text(reply)
            logger.info(f"LLM final reply: session={session.session_key} len={len(reply)} chars")

    return AgentResult(final_text=reply, turns=turn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_session(system_prompt: str, user_content: str) -> list[dict]:
    """Create a fresh messages list for a new session."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _inject_recent_history(messages: list[dict], session_key: str, bot, payload: dict) -> None:
    """Inject recent group chat history into a new session's messages list.

    Inserts a synthetic assistant tool_call + tool result pair at positions 1-2,
    so the LLM sees recent chat context without calling query_history itself.
    """
    if not payload.get("is_group", False):
        return

    group_id = payload.get("group_id")
    if group_id is None:
        return

    msgs = bot.msg_store.query(group_id=group_id, is_group=True, n=30)
    if not msgs:
        return

    from plugins.llm_memory import _format_msg

    bot_qq = bot.config.bot.qq
    lines = [_format_msg(m, bot_qq=bot_qq, include_time=True) for m in msgs]
    result_text = "找到以下最近消息：\n" + "\n".join(lines)

    call_id = f"auto_init_{session_key}"

    assistant_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "query_history",
                    "arguments": json.dumps({"limit": 30}, ensure_ascii=False),
                },
            }
        ],
    }

    tool_msg = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": result_text,
    }

    messages.insert(1, assistant_msg)
    messages.insert(2, tool_msg)


def _make_assistant_tool_msg(tool_calls: list) -> dict:
    """Convert ToolCall list to an OpenAI-format assistant message with tool_calls."""
    from src.core.llm import ToolCall as TC

    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id if isinstance(tc, TC) else tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc.name if isinstance(tc, TC) else tc.get("function", {}).get("name", ""),
                    "arguments": json.dumps(tc.arguments if isinstance(tc, TC) else tc.get("function", {}).get("arguments", {}), ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ],
    }
