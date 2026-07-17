from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles
from loguru import logger

from src.plugin.base import Event

if TYPE_CHECKING:
    from src.core.bot import Bot


class HistoryPlugin:
    """Log every message event (group + private) to logs/message-history.log
    as JSONL. Never consumes events -- always passes through."""

    name = "history"
    priority = -90

    log_path: Path = Path("logs/message-history.log")

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def match(self, event: Event) -> bool:
        return True

    async def handle(self, event: Event, bot: Bot) -> bool:
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

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(raw: Any) -> str | None:
        if hasattr(raw, "to_dict") and callable(raw.to_dict):
            return json.dumps(raw.to_dict(), ensure_ascii=False)
        if isinstance(raw, dict):
            return json.dumps(raw, ensure_ascii=False)
        return None

    def _ensure_log_dir(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
