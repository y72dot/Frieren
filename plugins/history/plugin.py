"""History package plugin – logs every message event to a JSONL file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles
from loguru import logger

from src.plugin import Event, EventResult, observe


class HistoryPlugin:
    __plugin_id__ = "history"
    name = "history"
    priority = -90

    log_path: Path = Path("logs/message-history.log")

    # -- Legacy interface (kept for test compatibility) --

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot: Any) -> bool:
        line = self._serialize(event.raw)
        if line is None:
            logger.warning("History: event.raw is not serializable, skipping")
            return False

        self._ensure_log_dir()
        async with aiofiles.open(self.log_path, "a", encoding="utf-8") as f:
            await f.write(line + "\n")

        logger.debug(
            f"History: logged {event.type} user={event.user_id} group={event.group_id}"
        )
        return False

    # -- New-style handler --

    @observe("*")
    async def observe_all(self, ctx, event, raw_msg) -> EventResult:
        line = self._serialize(event.raw)
        if line is None:
            return EventResult.CONTINUE

        self._ensure_log_dir()
        async with aiofiles.open(self.log_path, "a", encoding="utf-8") as f:
            await f.write(line + "\n")

        return EventResult.CONTINUE

    # -- helpers --

    @staticmethod
    def _serialize(raw: Any) -> str | None:
        if hasattr(raw, "to_dict") and callable(raw.to_dict):
            return json.dumps(raw.to_dict(), ensure_ascii=False)
        if isinstance(raw, dict):
            return json.dumps(raw, ensure_ascii=False)
        return None

    def _ensure_log_dir(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
