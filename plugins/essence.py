"""Essence plugin: set / remove group essence messages via reply + keyword."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from src.plugin.base import Event

if TYPE_CHECKING:
    from src.core.bot import Bot

_REPLY_PATTERN = re.compile(r"\[CQ:reply,id=(-?\d+)\]")
_CQ_PATTERN = re.compile(r"\[CQ:[^\]]+\]")

# Known error messages from QQ API (garbled on retrieval, match by errorCode)
_ESSENCE_ERRORS: dict[int, str] = {
    10003: "权限不足，仅群主/管理员可设精",
}


class EssencePlugin:
    name = "essence"
    priority = 50

    def match(self, event: Event) -> bool:
        return event.type == "message.group"

    async def handle(self, event: Event, bot: Bot) -> bool:
        message = event.message

        # Parse reply CQ code
        m = _REPLY_PATTERN.search(message)
        if not m:
            return False

        replied_msg_id = int(m.group(1))

        # Extract plain text after stripping all CQ codes
        plain = _CQ_PATTERN.sub("", message).strip()

        if plain == "设精":
            result = await bot.api.set_essence_msg(replied_msg_id)
            self._check_result(result, event, bot, "设精")
        elif plain == "寸止":
            result = await bot.api.delete_essence_msg(replied_msg_id)
            self._check_result(result, event, bot, "寸止")
        else:
            return False

        return False

    def _check_result(self, result: dict, event: Event, bot: Bot, action: str) -> None:
        """Log API errors and optionally notify the group."""
        inner = result.get("result", {})
        err_code = inner.get("errorCode", 0) if isinstance(inner, dict) else 0
        if err_code == 0:
            return

        wording = _ESSENCE_ERRORS.get(err_code, f"未知错误 (errorCode={err_code})")
        logger.warning(f"Essence {action} failed: {wording}")

        # Schedule a reply notification (non-blocking)
        group_id = event.group_id
        if group_id is not None:
            import asyncio

            asyncio.ensure_future(
                bot.api.send_group_msg(
                    group_id, f"[CQ:reply,id={event.message_id}]{wording}"
                )
            )
