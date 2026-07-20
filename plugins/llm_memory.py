"""LLM memory plugin: injects recent chat history into LLM conversation context."""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.message_bus import MessageType
from src.plugin.decorators import subscribe


@subscribe(MessageType.INTERNAL, priority=20)
async def llm_memory_handler(payload: dict[str, Any], bot) -> bool:
    """Handle ``llm_type: "context"`` INTERNAL messages – inject msg_store history."""
    if payload.get("llm_type") != "context":
        return False

    session_key: str = payload["session_key"]
    is_group: bool = payload.get("is_group", True)

    # Import here to access module-level _session_mgr from llm_core
    from plugins.llm_core import _session_mgr

    if _session_mgr is None:
        return False

    if is_group:
        group_id = int(session_key.split(":")[1])
        recent = bot.msg_store.recent(
            group_id, n=6, exclude_user_id=bot.config.bot.qq
        )
        if recent:
            lines = []
            for m in recent:
                name = m.nickname or f"user_{m.user_id}"
                lines.append(f"{name}: {m.content}")
            history_text = "[最近聊天记录]\n" + "\n".join(lines)
            await _session_mgr.add_context(session_key, "history", history_text)
            logger.debug(f"Injected {len(recent)} recent messages for [{session_key}]")
    else:
        user_id = int(session_key.split(":")[1])
        recent = bot.msg_store.recent_private(user_id, n=6)
        if recent:
            lines = [f"{m.nickname or 'user_' + str(m.user_id)}: {m.content}" for m in recent]
            history_text = "[最近聊天记录]\n" + "\n".join(lines)
            await _session_mgr.add_context(session_key, "history", history_text)

    return False
