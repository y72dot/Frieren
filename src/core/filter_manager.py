"""Unified message filtering subsystem (global + per-plugin).

Replaces the old ``FilterPlugin``. Mounted at ``bot.filter_mgr`` and
consulted by :class:`MessageBus` during dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.core.config import BotConfig, FilterModeConfig
    from src.plugin.base import Event


class FilterManager:
    """Unified message filtering (global + per-plugin).

    Global filtering blocks the entire event before any plugin sees it.
    Per-plugin filtering skips a specific plugin while letting others run.
    Admin users and the bot itself always bypass all filters.
    """

    def __init__(self, config: BotConfig | None = None) -> None:
        self._config = config

    def update_config(self, config: BotConfig) -> None:
        """Replace the current configuration (e.g. after hot-reload)."""
        self._config = config

    # ------------------------------------------------------------------
    # global filtering
    # ------------------------------------------------------------------

    def is_global_blocked(self, event: Event) -> bool:
        """Return ``True`` if *event* should be blocked entirely.

        Only message events are filtered; notices / requests / meta
        pass through.
        """
        if self._config is None:
            return False

        cfg = self._config.filter
        if not cfg.enable:
            return False

        if event.type not in ("message.group", "message.private"):
            return False

        if self._is_bypass(event):
            return False

        if event.is_group:
            blocked = self._apply_mode(cfg.group, event.group_id)
        else:
            blocked = self._apply_mode(cfg.private, event.user_id)

        if blocked:
            self._log_block("global", event)
        return blocked

    # ------------------------------------------------------------------
    # per-plugin filtering
    # ------------------------------------------------------------------

    def is_plugin_blocked(self, plugin_name: str, event: Event) -> bool:
        """Return ``True`` if *event* should be skipped for *plugin_name*.

        Only consults ``[filter.plugins.<name>]`` configuration.
        Admin / bot bypass applies here too.
        """
        if self._config is None:
            return False

        cfg = self._config.filter
        if not cfg.enable:
            return False

        if event.type not in ("message.group", "message.private"):
            return False

        if self._is_bypass(event):
            return False

        plugin_cfg = cfg.plugins.get(plugin_name)
        if plugin_cfg is None or not plugin_cfg.enable:
            return False

        if event.is_group:
            blocked = self._apply_mode(plugin_cfg.group, event.group_id)
        else:
            blocked = self._apply_mode(plugin_cfg.private, event.user_id)

        if blocked:
            self._log_block(f"plugin={plugin_name}", event)
        return blocked

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _is_bypass(self, event: Event) -> bool:
        """Admin users and the bot itself bypass all filters."""
        assert self._config is not None
        return (
            event.user_id in self._config.bot.admin_users
            or event.user_id == self._config.bot.qq
        )

    @staticmethod
    def _apply_mode(mode_cfg: FilterModeConfig, target_id: int | None) -> bool:
        """Return ``True`` if *target_id* should be blocked under *mode_cfg*."""
        if mode_cfg.mode == "off":
            return False
        if mode_cfg.mode == "whitelist":
            return target_id not in mode_cfg.list
        if mode_cfg.mode == "blacklist":
            return target_id in mode_cfg.list
        return False

    @staticmethod
    def _log_block(scope: str, event: Event) -> None:
        target = (
            f"group={event.group_id}" if event.is_group else f"user={event.user_id}"
        )
        preview = event.message[:80] if event.message else ""
        logger.debug(f"Filter blocked [{scope}]: {event.type} {target} msg='{preview}'")
