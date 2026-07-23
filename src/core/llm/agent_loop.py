"""Agent loop – ReAct and plan-execute modes for LLM-driven conversations."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from typing import Any

from loguru import logger

from src.core.llm import LlmResponse, LlmSessionLogger, ToolCall
from src.core.llm.circuit_breaker import CircuitBreaker
from src.core.llm.content_guard import contains_internal_tool_protocol, user_safe_text
from src.core.llm.session_manager import Session, SessionManager
from src.core.llm.tool_catalog import ToolCatalog
from src.core.llm.tool_executor import ToolExecutor
from src.core.llm.tool_permissions import ToolCallContext
from src.core.llm.tool_selector import ToolSelectionRequest, ToolSelector
from src.core.message_bus import BusMessage, MessageType


@dataclass
class LoopConfig:
    """Configuration for a single agent loop run."""

    max_turns: int = 8
    loop_mode: str = "react"        # "react" | "plan_execute"
    circuit_breaker_errors: int = 3
    circuit_breaker_same_tool: int = 5


@dataclass
class AgentResult:
    """Result returned by :meth:`AgentLoop.run`."""

    final_text: str = ""
    turns: int = 0
    tripped: bool = False
    error: str = ""
    tool_call_count: int = 0


class AgentLoop:
    """Orchestrates the LLM thought-action loop (replaces the inline
    for-turn block in ``llm_core_handler``)."""

    def __init__(
        self,
        catalog: ToolCatalog,
        session_mgr: SessionManager,
        executor: ToolExecutor,
        breaker: CircuitBreaker | None = None,
        config: LoopConfig | None = None,
        selector: ToolSelector | None = None,
    ) -> None:
        self.catalog = catalog
        self.session_mgr = session_mgr
        self.executor = executor
        self.breaker = breaker or CircuitBreaker()
        self.config = config or LoopConfig()
        self.selector = selector or ToolSelector()

    # ------------------------------------------------------------------
    # main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        session: Session,
        ctx: ToolCallContext,
        bot,
        *,
        session_log: LlmSessionLogger | None = None,
    ) -> AgentResult:
        """Execute the agent loop for *session*.

        Parameters
        ----------
        session:
            The conversation session (already primed with the latest user msg).
        ctx:
            Permission context for tool calls.
        bot:
            The Bot instance (for config access and message bus).
        """
        self.breaker.reset()
        effective = bot.config_center.config if getattr(bot, "config_center", None) else bot.config
        cfg = effective.llm
        session_log = session_log or LlmSessionLogger(session.session_key)

        max_turns = self.config.max_turns
        turn = 0
        tool_call_count = 0

        for turn in range(1, max_turns + 1):
            logger.debug(f"LLM turn {turn}/{max_turns}")
            session_log.turn_start(turn, max_turns)

            tool_view = self.selector.select(
                self.catalog,
                ctx,
                ToolSelectionRequest(
                    user_text=_latest_user_text(session.messages),
                    conversation_type="group" if ctx.group_id is not None else "private",
                ),
            )
            tools = tool_view.schemas()
            schema_bytes = len(
                json.dumps(tools, ensure_ascii=False).encode("utf-8")
            )
            self.executor.metrics.record_view(
                registered=self.catalog.count,
                visible=len(tool_view),
                schema_bytes=schema_bytes,
            )
            session_log.tool_view(tool_view.names, tool_view.active_packs)
            logger.debug(
                f"LLM tool view: session={session.session_key} "
                f"count={len(tool_view)} packs={','.join(tool_view.active_packs)}"
            )
            session_log.request(session.messages, cfg.model, len(tools))

            try:
                response = await bot.llm_provider.chat_completion(
                    session.messages,
                    tools=tools if tools else None,
                    model=cfg.model,
                    max_tokens=cfg.max_tokens,
                    temperature=cfg.temperature,
                )
            except Exception as exc:
                logger.opt(exception=True).error(f"LLM provider error: {exc}")
                return AgentResult(
                    final_text="抱歉，我遇到了一些问题，请稍后再试。",
                    turns=turn,
                    error=str(exc),
                    tool_call_count=tool_call_count,
                )

            # -- text response: conversation complete --
            if not response.tool_calls:
                raw_reply = response.text or ""
                reply = user_safe_text(raw_reply)
                if reply != raw_reply:
                    logger.error("Blocked internal tool protocol in LLM text response")
                session_log.text_response(reply)
                if reply.strip():
                    await self._emit_send(bot, ctx, reply)
                    session_log.final_text(reply)
                logger.info(f"LLM final reply: session={session.session_key} len={len(reply)} chars")
                return AgentResult(final_text=reply, turns=turn, tool_call_count=tool_call_count)

            # -- tool calls: execute and continue --
            session_log.tool_calls_result(response.tool_calls)
            self.executor.metrics.record_tool_calls(
                [call.name for call in response.tool_calls], set(tool_view.names)
            )
            tool_call_count += len(response.tool_calls)
            assistant_tool_msg = _make_assistant_tool_msg(response.tool_calls)
            session.messages.append(assistant_tool_msg)

            results = await self._execute_tools(
                response.tool_calls,
                ctx,
                bot,
                allowed_tool_names=frozenset(tool_view.names),
            )

            # Process tool results
            for result in results:
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
                session.messages.append(tool_msg)

                # Circuit breaker: record errors
                if "error" in str(result.get("result", "")):
                    tripped = self.breaker.record_error(
                        result.get("name", "?"), str(result["result"])
                    )
                    if tripped:
                        logger.warning("Circuit breaker tripped on errors")
                        return AgentResult(
                            final_text="抱歉，我遇到了一些问题，请稍后再试。",
                            turns=turn,
                            tripped=True,
                            tool_call_count=tool_call_count,
                        )

            # Circuit breaker: check for repeated identical tool calls
            for tc in response.tool_calls:
                if self.breaker.record_tool_call(tc.name, tc.arguments):
                    logger.warning(f"Circuit breaker: too many repeats of {tc.name}")
                    return AgentResult(
                        final_text="抱歉，我似乎陷入了循环，请重新描述你的需求。",
                        turns=turn,
                        tripped=True,
                        tool_call_count=tool_call_count,
                    )

        # -- Max turns reached: force final text reply --
        logger.warning("LLM agent reached max_turns, forcing final reply")
        session_log.max_turns_forced()
        final_messages = [
            *session.messages,
            {
                "role": "system",
                "content": (
                    "工具调用预算已经耗尽。现在必须直接给出纯文本最终答复；"
                    "不得调用工具，不得输出 DSML、tool_calls、XML 或函数调用协议。"
                    "如果证据不足，请明确说明不足。"
                ),
            },
        ]
        try:
            response = await bot.llm_provider.chat_completion(
                final_messages,
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
            )
        except Exception:
            response = LlmResponse(text="抱歉，处理超时，请稍后再试。")

        raw_reply = response.text or ""
        if response.tool_calls or contains_internal_tool_protocol(raw_reply):
            logger.error("Blocked tool call/protocol returned during forced final reply")
            raw_reply = ""
        reply = user_safe_text(raw_reply) if raw_reply else user_safe_text("<tool_call>")
        session_log.text_response(reply)
        if reply.strip():
            await self._emit_send(bot, ctx, reply)
            session_log.final_text(reply)
            logger.info(f"LLM final reply: session={session.session_key} len={len(reply)} chars")

        return AgentResult(final_text=reply, turns=turn, tripped=True, tool_call_count=tool_call_count)

    async def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        ctx: ToolCallContext,
        bot: Any,
        *,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute calls directly through persisted Invocation records."""
        results: list[dict[str, Any]] = []
        runtime = getattr(bot, "runtime", None)
        for call in tool_calls:
            step_id = ctx.step_id
            if runtime is not None and ctx.task_id and ctx.run_id:
                step = runtime.store.create_step(
                    ctx.run_id,
                    "tool",
                    input_data={"call_id": call.id, "name": call.name, "arguments": call.arguments},
                    status="RUNNING",
                )
                step_id = step.step_id
            call_ctx = replace(
                ctx,
                step_id=step_id,
                invocation_id=uuid.uuid4().hex,
            )
            result = await self.executor.execute(
                call.name,
                call.arguments,
                call_ctx,
                bot,
                allowed_tool_names=allowed_tool_names,
            )
            if runtime is not None and step_id != ctx.step_id:
                if "error" in result:
                    runtime.store.update_step(
                        step_id, "FAILED", output=result, error=str(result["error"])
                    )
                else:
                    runtime.store.update_step(step_id, "SUCCEEDED", output=result)
            results.append({"call_id": call.id, "name": call.name, "result": result})
        return results

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _emit_send(self, bot, ctx: ToolCallContext, text: str) -> None:
        """Emit an INTERNAL send message through the bus."""
        target_id = ctx.group_id if ctx.group_id else ctx.user_id
        await bot.message_bus.emit_and_wait(
            BusMessage(
                type=MessageType.INTERNAL,
                payload={
                    "topic": "send",
                    "llm_type": "send",
                    "target_id": target_id,
                    "is_group": ctx.group_id is not None,
                    "text": text,
                },
                source="agent_loop",
            ),
            bot,
        )


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


def _make_assistant_tool_msg(tool_calls: list[ToolCall]) -> dict:
    """Convert ToolCall list to an OpenAI-format assistant message."""
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
