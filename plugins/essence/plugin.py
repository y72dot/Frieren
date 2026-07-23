"""Essence package plugin – set/remove group essence messages via reply + keyword."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from src.plugin.base import Event
from src.plugin.definition import EventResult, on_event

_REPLY_PATTERN = re.compile(r"\[CQ:reply,id=(-?\d+)\]")
_CQ_PATTERN = re.compile(r"\[CQ:[^\]]+\]")

_ESSENCE_ERRORS: dict[int, str] = {
    10003: "权限不足，仅群主/管理员可设精",
}


class EssencePlugin:
    __plugin_id__ = "essence"
    name = "essence"
    priority = 51

    # -- Legacy interface (kept for test compatibility) --

    def match(self, event: Event) -> bool:
        return event.type == "message.group"

    async def handle(self, event: Event, bot: Any) -> bool:
        message = event.message
        m = _REPLY_PATTERN.search(message)
        if not m:
            return False

        replied_msg_id = int(m.group(1))
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

    def _check_result(self, result, event, bot: Any, action: str) -> None:
        inner = result.get("result", {})
        err_code = inner.get("errorCode", 0) if isinstance(inner, dict) else 0
        if err_code == 0:
            return

        wording = _ESSENCE_ERRORS.get(err_code, f"未知错误 (errorCode={err_code})")
        logger.warning(f"Essence {action} failed: {wording}")

        group_id = event.group_id
        if group_id is not None:
            asyncio.ensure_future(
                bot.api.send_group_msg(
                    group_id, f"[CQ:reply,id={event.message_id}]{wording}"
                )
            )

    # -- New-style handler --

    @on_event("message.group", priority=51)
    async def handle_essence(self, ctx, event, raw_msg) -> EventResult:
        message = event.message
        m = _REPLY_PATTERN.search(message)
        if not m:
            return EventResult.CONTINUE

        replied_msg_id = int(m.group(1))
        plain = _CQ_PATTERN.sub("", message).strip()

        if plain == "设精":
            result = await ctx.api.set_essence_msg(replied_msg_id)
            self._check_result_new(result, event, ctx, "设精")
        elif plain == "寸止":
            result = await ctx.api.delete_essence_msg(replied_msg_id)
            self._check_result_new(result, event, ctx, "寸止")
        else:
            return EventResult.CONTINUE

        return EventResult.CONTINUE

    def _check_result_new(self, result, event, ctx, action: str) -> None:
        inner = result.get("result", {})
        err_code = inner.get("errorCode", 0) if isinstance(inner, dict) else 0
        if err_code == 0:
            return

        wording = _ESSENCE_ERRORS.get(err_code, f"未知错误 (errorCode={err_code})")
        logger.warning(f"Essence {action} failed: {wording}")

        group_id = event.group_id
        if group_id is not None:
            asyncio.ensure_future(
                ctx.api.send_group_msg(
                    group_id, f"[CQ:reply,id={event.message_id}]{wording}"
                )
            )
