"""LLM gate plugin: @bot detection, access control, and LLM trigger emission."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.core.message_bus import BusMessage, MessageType
from src.core.message_store import _extract_nickname
from src.plugin.base import Event

if TYPE_CHECKING:
    from src.core.bot import Bot

_CQ_PATTERN = re.compile(r"\[CQ:[^\]]+\]")
_CQ_REPLY = re.compile(r"\[CQ:reply,id=(-?\d+)\]")


class LlmGatePlugin:
    """Gate plugin: detects @bot mentions and triggers LLM agent flow.

    Priority 5 ensures it runs after commands (p=0) but before
    essence/sticker (p=50). When it matches, it consumes the event
    so traditional plugins don't also try to handle it.
    """

    name = "llm_gate"
    priority = 5

    def match(self, event: Event) -> bool:
        return event.type == "message.private" or (
            event.type == "message.group" and "[CQ:at,qq=" in event.message
        )

    async def handle(self, event: Event, bot: Bot) -> bool:
        if not bot.config or not bot.config.llm.enabled:
            return False
        if bot.llm_provider is None:
            return False
        if event.user_id == bot.config.bot.qq:
            return False

        # Group messages: only respond when this bot is specifically @mentioned
        if event.is_group:
            bot_at = f"[CQ:at,qq={bot.config.bot.qq}]"
            if bot_at not in event.message:
                return False

        # Strip CQ codes but preserve reply info as readable text
        plain = _CQ_REPLY.sub(r"[回复\1]", event.message)
        plain = _CQ_PATTERN.sub("", plain).strip()
        if not plain:
            return False

        session_key = (
            f"group:{event.group_id}" if event.is_group else f"private:{event.user_id}"
        )
        nickname = _extract_nickname(event.raw, event.user_id)

        await bot.message_bus.emit_and_wait(
            BusMessage(
                type=MessageType.INTERNAL,
                payload={
                    "llm_type": "trigger",
                    "session_key": session_key,
                    "user_id": event.user_id,
                    "group_id": event.group_id,
                    "is_group": event.is_group,
                    "text": plain,
                    "nickname": nickname,
                    "message_id": event.message_id,
                },
                source="llm_gate",
            ),
            bot,
        )
        return True  # Consume event
