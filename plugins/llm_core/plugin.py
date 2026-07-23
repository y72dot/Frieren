"""LLM Core package plugin – thin MessageBus adapter for the LLM agent service."""

from __future__ import annotations

from typing import Any

from src.core.llm import agent_service as _service
from src.plugin.definition import EventResult, on_internal


class LlmCorePlugin:
    __plugin_id__ = "llm_core"
    name = "llm_core_handler"
    priority = 50

    @on_internal(topic="trigger")
    async def handle_trigger(self, payload: dict[str, Any], ctx) -> EventResult:
        """Forward LLM trigger messages to :class:`LlmAgentService`."""
        bot = ctx._bot if hasattr(ctx, "_bot") else ctx
        result = await _service.handle_trigger(payload, bot)
        return EventResult.from_bool(result)
