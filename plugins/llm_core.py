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
    ensure_platform = getattr(bot, "ensure_tool_platform", None)
    if ensure_platform is not None:
        ensure_platform()
        _tools_registry = bot.tool_catalog.get_all_defs()
    else:
        from plugins.llm_tools import TOOL_DEFS

        _tools_registry = TOOL_DEFS
    logger.debug(f"LLM tools registered: {len(_tools_registry)} tool(s)")

    # Initialise agent subsystems on bot if not already done
    if not hasattr(bot, "_agent_initialized"):
        _init_bot_agent(bot)
        bot._agent_initialized = True


def _init_bot_agent(bot) -> None:
    """Create and attach AgentLoop + SessionManager to the bot."""
    ensure_platform = getattr(bot, "ensure_tool_platform", None)
    if ensure_platform is not None:
        ensure_platform()
        catalog = bot.tool_catalog
        executor = bot.tool_executor
    else:
        from plugins.llm_tools import register_llm_tools
        from src.core.llm.tool_catalog import ToolCatalog
        from src.core.llm.tool_executor import ToolExecutor

        catalog = ToolCatalog()
        register_llm_tools(catalog)
        executor = ToolExecutor(catalog)

    effective = bot.config_center.config if getattr(bot, "config_center", None) else bot.config
    cfg = effective.llm

    config_center = getattr(bot, "config_center", None)
    persistent = bool(config_center and config_center.persistent)
    # Session manager (persistence + pruning)
    session_mgr = SessionManager(
        db_path="data/llm_state.db" if persistent else ":memory:",
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
        catalog=catalog,
        session_mgr=session_mgr,
        executor=executor,
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
    effective = bot.config_center.config if getattr(bot, "config_center", None) else bot.config
    cfg = effective.llm

    rendered_prompt = _render_system_prompt(bot, payload)
    snapshot = None
    if getattr(bot, "config_center", None) is not None:
        snapshot = bot.config_center.create_snapshot(
            prompt_version=rendered_prompt.version,
            prompt_text=rendered_prompt.text,
            context_key=session_key,
        )
        payload["config_snapshot_id"] = snapshot.snapshot_id
        logger.debug(
            f"LLM config snapshot: session={session_key} "
            f"snapshot={snapshot.snapshot_id} prompt={rendered_prompt.version}"
        )

    owns_run = False
    task_id = str(payload.get("task_id", ""))
    run_id = str(payload.get("run_id", ""))
    step_id = str(payload.get("step_id", ""))
    ensure_runtime = getattr(bot, "ensure_runtime_platform", None)
    if ensure_runtime is not None:
        ensure_runtime()
    runtime = getattr(bot, "runtime", None)
    if runtime is not None and not run_id:
        task, run = runtime.create_task_run(
            goal=text,
            trigger_type="qq_message",
            trigger_event_id=str(payload.get("event_id", "")) or None,
            template={"kind": "agent_prompt", "goal": text, "prompt": text},
            requested_by=payload["user_id"],
            conversation_type="group" if is_group else "private",
            conversation_id=payload.get("group_id") if is_group else payload["user_id"],
            config_snapshot_id=snapshot.snapshot_id if snapshot else "",
            prompt_version=rendered_prompt.version,
        )
        runtime.store.update_task(task.task_id, "RUNNING")
        runtime.store.update_run(run.run_id, "RUNNING")
        step = runtime.store.create_step(
            run.run_id,
            "agent_loop",
            input_data={"session_key": session_key, "text": text},
            status="RUNNING",
        )
        task_id, run_id, step_id = task.task_id, run.run_id, step.step_id
        owns_run = True
        payload.update({"task_id": task_id, "run_id": run_id, "step_id": step_id})

    # Build user content
    user_content = f"{nickname}: {text}" if is_group else text

    # Reuse or create session
    session_mgr: SessionManager = getattr(bot, "session_mgr", None)
    if session_mgr is not None:
        # Honour live configuration changes and keep the legacy cache as a
        # compatibility view for existing integrations.
        session_mgr._ttl = cfg.session_ttl
        if cfg.session_ttl <= 0:
            session_mgr._cache.pop(session_key, None)
        legacy_entry = _session_cache.get(session_key)
        managed_entry = session_mgr._cache.get(session_key)
        if (
            legacy_entry is not None
            and managed_entry is not None
            and legacy_entry[0] < managed_entry.last_active
        ):
            session_mgr._cache.pop(session_key, None)
        session = session_mgr.get_or_create(session_key, rendered_prompt.text, user_content)
        _replace_system_prompt(session.messages, rendered_prompt.text)
        is_new = session.turn_count == 0
        if is_new:
            logger.info(f"LLM session start: key={session_key} nickname={nickname} text={text[:80]}")
            _inject_recent_history(session.messages, session_key, bot, payload)
            session_log.session_new(len(session.messages))
        else:
            logger.info(f"LLM session reuse: key={session_key} nickname={nickname} text={text[:80]}")
            session_log.session_reuse(len(session.messages), _time.time() - session.last_active)
        _session_cache[session_key] = (session.last_active, session.messages)
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
            messages = _new_session(rendered_prompt.text, user_content)
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
        task_id=task_id or None,
        run_id=run_id or session_key,
        step_id=step_id or None,
        trace_id=str(payload.get("trace_id", "")),
        config_snapshot_id=str(payload.get("config_snapshot_id", "")),
    )

    # Run the agent loop
    agent_loop: AgentLoop | None = getattr(bot, "agent_loop", None)
    if agent_loop is not None:
        result = await agent_loop.run(session, ctx, bot, session_log=session_log)
    else:
        # Fallback: inline loop for tests without agent_loop
        result = await _inline_loop(session, ctx, bot, payload, session_log)

    # Post-loop: update session state
    session.turn_count += result.turns
    session.last_active = _time.time()
    if session_mgr is not None:
        session_mgr.save(session)
    _session_cache[session_key] = (session.last_active, session.messages)

    if owns_run:
        output = {
            "final_text": result.final_text,
            "turns": result.turns,
            "tool_call_count": result.tool_call_count,
            "tripped": result.tripped,
        }
        if result.error:
            runtime.store.update_step(step_id, "FAILED", output=output, error=result.error)
            runtime.store.update_run(run_id, "FAILED", error=result.error)
            runtime.store.update_task(task_id, "FAILED", error=result.error)
        else:
            runtime.store.update_step(step_id, "SUCCEEDED", output=output)
            runtime.store.update_run(run_id, "SUCCEEDED")
            runtime.store.update_task(task_id, "SUCCEEDED")

    logger.info(f"LLM session end: key={session_key} turns={result.turns} tool_calls={result.tool_call_count}")
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
    tool_call_count = 0

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
            return AgentResult(final_text=reply, turns=turn, tool_call_count=tool_call_count)

        session_log.tool_calls_result(response.tool_calls)
        tool_call_count += len(response.tool_calls)
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
                    "task_id": ctx.task_id,
                    "run_id": ctx.run_id,
                    "step_id": ctx.step_id,
                    "trace_id": ctx.trace_id,
                    "config_snapshot_id": ctx.config_snapshot_id,
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

    return AgentResult(final_text=reply, turns=turn, tool_call_count=tool_call_count)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_session(system_prompt: str, user_content: str) -> list[dict]:
    """Create a fresh messages list for a new session."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _render_system_prompt(bot, payload: dict[str, Any]):
    """Render the configured prompt profile for the current QQ context."""
    registry = getattr(bot, "prompt_registry", None)
    if registry is None:
        from src.core.prompts import PromptRegistry

        registry = PromptRegistry.from_legacy(bot.config.llm.system_prompt)
        bot.prompt_registry = registry
    profile = bot.config.llm.prompts.profile if bot.config.llm.prompts.enabled else "default"
    return registry.render(
        profile,
        {
            "bot_qq": bot.config.bot.qq,
            "bot_name": bot.config.bot.nickname[0] if bot.config.bot.nickname else str(bot.config.bot.qq),
            "conversation_type": "group" if payload.get("is_group") else "private",
            "conversation_id": payload.get("group_id") or payload.get("user_id", ""),
        },
    )


def _replace_system_prompt(messages: list[dict], prompt: str) -> None:
    """Apply the current prompt version to a recovered or reused session."""
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = prompt
    else:
        messages.insert(0, {"role": "system", "content": prompt})


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
    from src.core.llm import ToolCall as ToolCallType

    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id if isinstance(tc, ToolCallType) else tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc.name if isinstance(tc, ToolCallType) else tc.get("function", {}).get("name", ""),
                    "arguments": json.dumps(tc.arguments if isinstance(tc, ToolCallType) else tc.get("function", {}).get("arguments", {}), ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ],
    }
