"""Sticker react plugin: react to a replied message with a sticker/emoji."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from src.plugin.base import Event

if TYPE_CHECKING:
    from src.core.bot import Bot

_REPLY_PATTERN = re.compile(r"\[CQ:reply,id=(-?\d+)\]")
_CQ_PATTERN = re.compile(r"\[CQ:[^\]]+\]")
_FACE_PATTERN = re.compile(r"\[CQ:face,id=(\d+)")


class StickerReactPlugin:
    name = "sticker_react"
    priority = 50

    def match(self, event: Event) -> bool:
        return event.type == "message.group"

    async def handle(self, event: Event, bot: Bot) -> bool:
        message = event.message

        # Ignore bot's own messages
        if event.user_id == bot.config.bot.qq:
            logger.debug("StickerReact: ignoring own message")
            return False

        # Parse reply CQ code
        m = _REPLY_PATTERN.search(message)
        if not m:
            logger.debug("StickerReact: no reply code found")
            return False

        replied_msg_id = int(m.group(1))

        # Remove the reply CQ code; keep CQ codes for face extraction
        after_reply = _REPLY_PATTERN.sub("", message, count=1)

        # Strip all CQ codes to get plain text for keyword matching
        plain = _CQ_PATTERN.sub("", after_reply).strip()
        if not plain.startswith("贴"):
            logger.debug(f"StickerReact: message does not start with 贴: {plain!r}")
            return False

        # Remove "贴" prefix + optional whitespace from plain text
        remainder_plain = plain[1:].lstrip()

        # Extract emoji ID
        emoji_id: int | None = None
        face_m = _FACE_PATTERN.search(after_reply)
        if face_m:
            emoji_id = int(face_m.group(1))
        elif remainder_plain:
            # Unicode/system emoji: QQ uses the decimal codepoint as emoji_id
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
