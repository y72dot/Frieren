"""Thin MessageBus adapter for the core LLM agent service."""

from __future__ import annotations

import sys
from typing import Any

from src.core.llm import agent_service as _service
from src.core.message_bus import MessageType
from src.plugin.decorators import subscribe


@subscribe(MessageType.INTERNAL, priority=50)
async def llm_core_handler(payload: dict[str, Any], bot) -> bool:
    """Forward LLM trigger messages to :class:`LlmAgentService`."""
    return await _service.handle_trigger(payload, bot)


# Keep legacy private imports and module-level cache access working during the
# migration window.  The orchestration implementation remains in core.
_service.llm_core_handler = llm_core_handler
sys.modules[__name__] = _service
