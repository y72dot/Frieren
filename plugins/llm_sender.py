"""LLM sender plugin: formats, chunks, and sends LLM replies."""

from __future__ import annotations

from time import time
from typing import Any

from src.core.message_bus import MessageType
from src.plugin.decorators import subscribe

_QQ_MSG_LIMIT = 4000


@subscribe(MessageType.INTERNAL, priority=40)
async def llm_sender_handler(payload: dict[str, Any], bot) -> bool:
    """Handle ``llm_type: "send"`` INTERNAL messages – send chunked text."""
    if payload.get("llm_type") != "send":
        return False

    target_id: int = payload["target_id"]
    is_group: bool = payload["is_group"]
    text: str = payload["text"]

    bot_qq = bot.config.bot.qq
    bot_nickname = (
        bot.config.bot.nickname[0] if bot.config.bot.nickname else str(bot_qq)
    )

    for chunk in _split_message(text, _QQ_MSG_LIMIT):
        if is_group:
            response = await bot.api.send_group_msg(target_id, chunk)
            msg_id = response.get("message_id") if isinstance(response, dict) else None
            if msg_id is not None:
                bot.msg_store.record_bot_message(
                    message_id=msg_id,
                    group_id=target_id,
                    user_id=bot_qq,
                    nickname=bot_nickname,
                    content=chunk,
                    time=int(time()),
                    is_group=True,
                )
        else:
            response = await bot.api.send_private_msg(target_id, chunk)
            msg_id = response.get("message_id") if isinstance(response, dict) else None
            if msg_id is not None:
                bot.msg_store.record_bot_message(
                    message_id=msg_id,
                    group_id=None,
                    user_id=bot_qq,
                    nickname=bot_nickname,
                    content=chunk,
                    time=int(time()),
                    is_group=False,
                )

    return False


def _split_message(text: str, limit: int) -> list[str]:
    """Split text into chunks not exceeding *limit* characters, preferring newline boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while len(text) > limit:
        pos = text.rfind("\n", 0, limit)
        if pos == -1 or pos < limit // 2:
            pos = limit
        chunks.append(text[:pos])
        text = text[pos:].lstrip()
    if text:
        chunks.append(text)
    return chunks
