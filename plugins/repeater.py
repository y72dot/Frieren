"""Repeater plugin: repeats the latest group message when the two most recent
messages (excluding the bot) come from different users and have identical content."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from src.plugin.base import Event

if TYPE_CHECKING:
    from src.core.bot import Bot

# Track last repeated content per group to prevent duplicate repeats
_last_repeated: dict[int, str] = {}

# Per-group lock to prevent race conditions from duplicate napcat events
_locks: dict[int, asyncio.Lock] = {}


class RepeaterPlugin:
    name = "repeater"
    priority = 100

    def match(self, event: Event) -> bool:
        return event.type == "message.group"

    async def handle(self, event: Event, bot: Bot) -> bool:
        # 1. Skip bot's own messages (infinite loop prevention)
        if event.user_id == bot.config.bot.qq:
            logger.debug("repeater: self-message, skipping")
            return False

        stripped = event.message.strip()
        # 2. Skip empty messages (pure image/sticker)
        if not stripped:
            return False

        group_id = event.group_id
        if group_id is None:
            return False

        # 3. Per-group lock to prevent race conditions from duplicate napcat events
        lock = _locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            # 4. Query last 2 non-bot messages for this group.
            #    In production EventBus already recorded the current event,
            #    so the most recent entry IS the current message.
            recent_msgs = bot.msg_store.recent(
                group_id, n=2, exclude_user_id=bot.config.bot.qq
            )

            # 5. Need at least 2 non-bot messages to form a pair
            if len(recent_msgs) < 2:
                return False

            msg_prev = recent_msgs[-2]
            msg_curr = recent_msgs[-1]

            # 6. Same user -> no repeat
            if msg_prev.user_id == msg_curr.user_id:
                logger.debug(
                    f"repeater: same_user grp={group_id} uid={msg_curr.user_id}, skipping"
                )
                return False

            # 7. Different content -> no repeat
            if msg_prev.content != msg_curr.content:
                return False

            last_content = msg_curr.content
            # 8. Already repeated this content?
            if _last_repeated.get(group_id) == last_content:
                logger.debug(
                    f"repeater: already_repeated grp={group_id} content={last_content[:30]}, skipping"
                )
                return False

            # 9. Commit state and repeat
            _last_repeated[group_id] = last_content
            logger.info(
                f"repeater: repeat grp={group_id} uid={event.user_id} content={last_content[:50]}"
            )

            await bot.api.send_group_msg(group_id, last_content)

        # 10. Never consume the event
        return False
