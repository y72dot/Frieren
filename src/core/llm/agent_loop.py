"""Agent loop – ReAct and plan-execute modes for LLM-driven conversations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.core.llm import LlmResponse, LlmSessionLogger, ToolCall
from src.core.llm.circuit_breaker import CircuitBreaker
from src.core.llm.session_manager import Session, SessionManager
from src.core.llm.tool_catalog import ToolCatalog
from src.core.llm.tool_executor import ToolExecutor
from src.core.llm.tool_permissions import ToolCallContext
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
    ) -> None:
        self.catalog = catalog
        self.session_mgr = session_mgr
        self.executor = executor
        self.breaker = breaker or CircuitBreaker()
        self.config = config or LoopConfig()

    # ------------------------------------------------------------------
    # main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        session: Session,
        ctx: ToolCallContext,
        bot,
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
        cfg = bot.config.llm
        session_log = LlmSessionLogger(session.session_key)

        max_turns = self.config.max_turns
        turn = 0
        tool_call_count = 0

        for turn in range(1, max_turns + 1):
            logger.debug(f"LLM turn {turn}/{max_turns}")
            session_log.turn_start(turn, max_turns)

            tools = self.catalog.get_defs(ctx.user_is_admin)
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
                reply = response.text or ""
                session_log.text_response(reply)
                if reply.strip():
                    await self._emit_send(bot, ctx, reply)
                    session_log.final_text(reply)
                logger.info(f"LLM final reply: session={session.session_key} len={len(reply)} chars")
                return AgentResult(final_text=reply, turns=turn, tool_call_count=tool_call_count)

            # -- tool calls: execute and continue --
            session_log.tool_calls_result(response.tool_calls)
            tool_call_count += len(response.tool_calls)
            assistant_tool_msg = _make_assistant_tool_msg(response.tool_calls)
            session.messages.append(assistant_tool_msg)

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
                        "user_id": ctx.user_id,
                    },
                    source="agent_loop",
                ),
                bot,
            )

            # Process tool results
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
        try:
            response = await bot.llm_provider.chat_completion(
                session.messages,
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
            )
        except Exception:
            response = LlmResponse(text="抱歉，处理超时，请稍后再试。")

        reply = response.text or ""
        session_log.text_response(reply)
        if reply.strip():
            await self._emit_send(bot, ctx, reply)
            session_log.final_text(reply)
            logger.info(f"LLM final reply: session={session.session_key} len={len(reply)} chars")

        return AgentResult(final_text=reply, turns=turn, tripped=True, tool_call_count=tool_call_count)

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
                    "llm_type": "send",
                    "target_id": target_id,
                    "is_group": ctx.group_id is not None,
                    "text": text,
                },
                source="agent_loop",
            ),
            bot,
        )


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
