"""LLM Sender package plugin – formats, chunks, and sends LLM replies."""

from __future__ import annotations

from time import time
from typing import Any

from loguru import logger

from src.core.llm.content_guard import user_safe_text
from src.plugin import EventResult, on_internal

_QQ_MSG_LIMIT = 4000


class LlmSenderPlugin:
    __plugin_id__ = "llm_sender"
    name = "llm_sender_handler"
    priority = 40

    @on_internal(topic="send")
    async def handle_send(self, payload: dict[str, Any], ctx) -> EventResult:
        """Handle ``llm_type: "send"`` INTERNAL messages – send chunked text."""
        if payload.get("llm_type") != "send":
            return EventResult.CONTINUE

        target_id: int = payload["target_id"]
        is_group: bool = payload["is_group"]
        raw_text: str = payload["text"]
        text = user_safe_text(raw_text)
        if text != raw_text:
            logger.error("llm_sender blocked internal tool protocol from outbound message")

        bot_qq = ctx.config.bot_id
        bot_nickname = ctx.config.nickname

        chunks = _split_message(text, _QQ_MSG_LIMIT)
        scope = "group" if is_group else "private"
        logger.info(f"llm_sender: sending {len(chunks)} chunk(s) to {scope}:{target_id}")

        for i, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                logger.debug(f"llm_sender: chunk {i}/{len(chunks)} len={len(chunk)}")
            if is_group:
                response = await ctx.api.send_group_msg(target_id, chunk)
                msg_id = response.get("message_id") if isinstance(response, dict) else None
                if msg_id is not None:
                    ctx.record_bot_message(
                        message_id=msg_id,
                        group_id=target_id,
                        user_id=bot_qq,
                        nickname=bot_nickname,
                        content=chunk,
                        time=int(time()),
                        is_group=True,
                    )
            else:
                response = await ctx.api.send_private_msg(target_id, chunk)
                msg_id = response.get("message_id") if isinstance(response, dict) else None
                if msg_id is not None:
                    ctx.record_bot_message(
                        message_id=msg_id,
                        group_id=None,
                        user_id=bot_qq,
                        nickname=bot_nickname,
                        content=chunk,
                        time=int(time()),
                        is_group=False,
                        peer_id=target_id,
                    )

        return EventResult.CONTINUE


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
