"""Repeater package plugin – repeats matching consecutive messages."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.plugin import Event, EventResult, on_event

_last_repeated: dict[int, str] = {}
_locks: dict[int, asyncio.Lock] = {}


class RepeaterPlugin:
    __plugin_id__ = "repeater"
    name = "repeater"
    priority = 100

    # -- Legacy interface (kept for test compatibility) --

    def match(self, event: Event) -> bool:
        return event.type == "message.group"

    async def handle(self, event: Event, bot: Any) -> bool:
        if event.user_id == bot.config.bot.qq:
            logger.debug("repeater: self-message, skipping")
            return False

        stripped = event.message.strip()
        if not stripped:
            return False

        group_id = event.group_id
        if group_id is None:
            return False

        lock = _locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            recent_msgs = bot.msg_store.recent(
                group_id, n=2, exclude_user_id=bot.config.bot.qq
            )

            if len(recent_msgs) < 2:
                return False

            msg_prev = recent_msgs[-2]
            msg_curr = recent_msgs[-1]

            if msg_prev.user_id == msg_curr.user_id:
                logger.debug(
                    f"repeater: same_user grp={group_id} uid={msg_curr.user_id}, skipping"
                )
                return False

            if msg_prev.content != msg_curr.content:
                return False

            last_content = msg_curr.content
            if _last_repeated.get(group_id) == last_content:
                logger.debug(
                    f"repeater: already_repeated grp={group_id} content={last_content[:30]}, skipping"
                )
                return False

            _last_repeated[group_id] = last_content
            logger.info(
                f"repeater: repeat grp={group_id} uid={event.user_id} content={last_content[:50]}"
            )

            await bot.api.send_group_msg(group_id, last_content)

        return False

    # -- New-style handler --

    @on_event("message.group", priority=100)
    async def handle_repeat(self, ctx, event, raw_msg) -> EventResult:
        if event.user_id == ctx.config.bot_id:
            return EventResult.CONTINUE

        stripped = event.message.strip()
        if not stripped:
            return EventResult.CONTINUE

        group_id = event.group_id
        if group_id is None:
            return EventResult.CONTINUE

        lock = _locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            recent_msgs = await ctx.get_recent_messages(
                group_id, n=2, exclude_user_id=ctx.config.bot_id
            )

            if len(recent_msgs) < 2:
                return EventResult.CONTINUE

            msg_prev = recent_msgs[-2]
            msg_curr = recent_msgs[-1]

            if msg_prev.user_id == msg_curr.user_id:
                return EventResult.CONTINUE

            if msg_prev.content != msg_curr.content:
                return EventResult.CONTINUE

            last_content = msg_curr.content
            if _last_repeated.get(group_id) == last_content:
                return EventResult.CONTINUE

            _last_repeated[group_id] = last_content
            logger.info(
                f"repeater: repeat grp={group_id} uid={event.user_id} content={last_content[:50]}"
            )

            await ctx.api.send_group_msg(group_id, last_content)

        return EventResult.CONTINUE
