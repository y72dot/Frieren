"""StickerReact package plugin – reacts to replied messages with stickers/emoji."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from src.plugin.base import Event
from src.plugin.definition import EventResult, on_event

_REPLY_PATTERN = re.compile(r"\[CQ:reply,id=(-?\d+)\]")
_CQ_PATTERN = re.compile(r"\[CQ:[^\]]+\]")
_FACE_PATTERN = re.compile(r"\[CQ:face,id=(\d+)")


class StickerReactPlugin:
    __plugin_id__ = "sticker_react"
    name = "sticker_react"
    priority = 50

    # -- Legacy interface (kept for test compatibility) --

    def match(self, event: Event) -> bool:
        return event.type == "message.group"

    async def handle(self, event: Event, bot: Any) -> bool:
        return await self._handle_impl(event, bot)

    async def _handle_impl(self, event: Event, bot: Any) -> bool:
        message = event.message

        if event.user_id == bot.config.bot.qq:
            logger.debug("StickerReact: ignoring own message")
            return False

        m = _REPLY_PATTERN.search(message)
        if not m:
            logger.debug("StickerReact: no reply code found")
            return False

        replied_msg_id = int(m.group(1))
        after_reply = _REPLY_PATTERN.sub("", message, count=1)
        plain = _CQ_PATTERN.sub("", after_reply).strip()
        if not plain.startswith("贴"):
            logger.debug(f"StickerReact: message does not start with 贴: {plain!r}")
            return False

        remainder_plain = plain[1:].lstrip()
        emoji_id: int | None = None
        face_m = _FACE_PATTERN.search(after_reply)
        if face_m:
            emoji_id = int(face_m.group(1))
        elif remainder_plain:
            emoji_id = ord(remainder_plain[0])

        if emoji_id is None:
            logger.debug("StickerReact: no emoji content after 贴")
            return False

        try:
            await bot.api.call_action(
                "set_msg_emoji_like",
                message_id=replied_msg_id,
                emoji_id=emoji_id,
                set=True,
            )
            logger.info(
                f"StickerReact: reacted with {emoji_id!r} to message {replied_msg_id}"
            )
        except Exception as e:
            logger.warning(f"StickerReact: API call failed: {e}")

        return False

    # -- New-style handler --

    @on_event("message.group", priority=50)
    async def handle_sticker(self, ctx, event, raw_msg) -> EventResult:
        result = await self._handle_new(event, ctx)
        return EventResult.CONSUME if result else EventResult.CONTINUE

    async def _handle_new(self, event, ctx) -> bool:
        message = event.message
        if event.user_id == ctx.config.bot_id:
            return False

        m = _REPLY_PATTERN.search(message)
        if not m:
            return False

        replied_msg_id = int(m.group(1))
        after_reply = _REPLY_PATTERN.sub("", message, count=1)
        plain = _CQ_PATTERN.sub("", after_reply).strip()
        if not plain.startswith("贴"):
            return False

        remainder_plain = plain[1:].lstrip()
        emoji_id: int | None = None
        face_m = _FACE_PATTERN.search(after_reply)
        if face_m:
            emoji_id = int(face_m.group(1))
        elif remainder_plain:
            emoji_id = ord(remainder_plain[0])

        if emoji_id is None:
            return False

        import contextlib
        with contextlib.suppress(Exception):
            await ctx.api.call_action(
                "set_msg_emoji_like",
                message_id=replied_msg_id,
                emoji_id=emoji_id,
                set=True,
            )

        return False
