"""Repeater plugin: repeats the latest group message when the two most recent
messages come from different users and have identical content."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.plugin.base import Event, Plugin

if TYPE_CHECKING:
    from src.core.bot import Bot

# Per-group history: group_id → list of (user_id, message), max 2 entries
_group_history: dict[int, list[tuple[int, str]]] = {}

# Track last repeated message per group to prevent duplicate repeats
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
            return False

        stripped = event.message.strip()
        # 2. Skip empty messages (pure image/sticker)
        if not stripped:
            return False

        group_id = event.group_id
        if group_id is None:
            return False

        # Lock per group to prevent race conditions from duplicate napcat events
        lock = _locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            # 3. Record to history, keep last 2
            history = _group_history.setdefault(group_id, [])
            history.append((event.user_id, stripped))
            if len(history) > 2:
                history[:] = history[-2:]

            # 4. Need at least 2 messages
            if len(history) < 2:
                return False

            # 5. Same user → no repeat
            if history[0][0] == history[1][0]:
                return False

            # 5.5. Different content → no repeat
            if history[0][1] != history[1][1]:
                history.clear()
                return False

            # 6. Different users, same message → check if already repeated this content
            last_msg = history[1][1]
            if _last_repeated.get(group_id) == last_msg:
                history.clear()
                return False

            # Committing: update state before I/O so dups don't race in
            _last_repeated[group_id] = last_msg
            history.clear()

            await bot.api.send_group_msg(group_id, last_msg)

        # 7. Never consume the event
        return False
