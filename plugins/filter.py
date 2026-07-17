"""Pre-filter plugin: whitelist/blacklist group and private messages.

Runs at priority=-100, before all other plugins. Returns True from handle()
to consume (block) the event, False to pass it through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from src.plugin.base import Event

if TYPE_CHECKING:
    from src.core.bot import Bot
    from src.core.config import FilterModeConfig


class FilterPlugin:
    """Pre-filter that checks every message event against group/private
    whitelist or blacklist configured in ``[filter]`` section of bot.toml."""

    name = "filter"
    priority = -100

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def match(self, event: Event) -> bool:
        """Only intercept message events; notices / requests / meta pass through."""
        return event.type in ("message.group", "message.private")

    async def handle(self, event: Event, bot: Bot) -> bool:
        """Return True to block the event, False to allow it."""
        cfg = bot.config.filter

        if not cfg.enable:
            return False

        # Admin users always bypass the filter.
        if event.user_id in bot.config.bot.admin_users:
            return False

        # Bot's own messages always bypass.
        if event.user_id == bot.config.bot.qq:
            return False

        msg_preview = event.message[:80] if event.message else ""
        if event.is_group:
            blocked = self._check(cfg.group, event.group_id)
            target = f"group={event.group_id}"
        else:
            blocked = self._check(cfg.private, event.user_id)
            target = f"user={event.user_id}"

        if blocked:
            logger.debug(
                f"Filter blocked: {event.type} {target} "
                f"msg='{msg_preview}'"
            )
        return blocked

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    @staticmethod
    def _check(mode_cfg: FilterModeConfig, target_id: int | None) -> bool:
        """Return True if *target_id* should be blocked."""
        if mode_cfg.mode == "off":
            return False
        if mode_cfg.mode == "whitelist":
            # Block if target is NOT in the whitelist.
            return target_id not in mode_cfg.list
        if mode_cfg.mode == "blacklist":
            # Block if target IS in the blacklist.
            return target_id in mode_cfg.list
        return False
