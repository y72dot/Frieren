"""Plugin registry, auto-discovery, and event dispatch."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.plugin.base import Plugin

if TYPE_CHECKING:
    from src.core.bot import Bot
    from src.plugin.base import Event


class PluginManager:
    """Manages plugin lifecycle: registration, discovery, and event dispatch."""

    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def plugins(self) -> list[Plugin]:
        """Return a shallow copy of the registered plugin list."""
        return list(self._plugins)

    @property
    def plugin_count(self) -> int:
        """Number of registered plugins."""
        return len(self._plugins)

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------

    def register(self, plugin: Plugin) -> None:
        """Register a plugin and re-sort by priority."""
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)
        logger.debug(f"Plugin registered: {plugin.name} (priority={plugin.priority})")

    def unregister(self, plugin_name: str) -> bool:
        """Remove a plugin by name. Returns ``True`` if a plugin was removed."""
        before = len(self._plugins)
        self._plugins = [p for p in self._plugins if p.name != plugin_name]
        removed = before != len(self._plugins)
        if removed:
            logger.info(f"Plugin unregistered: {plugin_name}")
        else:
            logger.warning(f"Plugin not found for unregister: {plugin_name}")
        return removed

    # ------------------------------------------------------------------
    # auto-discovery
    # ------------------------------------------------------------------

    def auto_discover(
        self,
        plugin_dirs: list[str],
        disabled: list[str] | None = None,
    ) -> int:
        """Scan *plugin_dirs* for decorated functions and register them.

        Returns the number of newly registered plugins.
        """
        if disabled is None:
            disabled = []

        count_before = self.plugin_count

        for dir_name in plugin_dirs:
            path = Path(dir_name).resolve()
            if not path.is_dir():
                logger.warning(f"Plugin directory not found: {dir_name}")
                continue

            # Build a package name from the directory name (e.g. "plugins").
            pkg_name = path.name

            for py_file in sorted(path.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue

                module_name = f"{pkg_name}.{py_file.stem}"
                self._import_and_register(module_name, disabled)

        newly_registered = self.plugin_count - count_before
        logger.info(
            f"Plugin auto-discovery complete: {newly_registered} plugin(s) loaded "
            f"(total: {self.plugin_count})"
        )
        return newly_registered

    def _import_and_register(
        self,
        module_name: str,
        disabled: list[str],
    ) -> None:
        """Import a single module and register any decorated plugins found."""
        try:
            module = importlib.import_module(module_name)
        except Exception:
            logger.opt(exception=True).error(
                f"Failed to import plugin module: {module_name}"
            )
            return

        for _, obj in inspect.getmembers(module, inspect.isfunction):
            plugin = getattr(obj, "__plugin__", None)
            if plugin is None:
                continue
            if not isinstance(plugin, Plugin):
                continue
            if plugin.name in disabled:
                logger.debug(f"Skipping disabled plugin: {plugin.name}")
                continue
            self.register(plugin)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, event: Event, bot: Bot) -> bool:
        """Route *event* to the first matching plugin, in priority order.

        Returns ``True`` if a plugin consumed the event, ``False`` otherwise.
        """
        for plugin in self._plugins:
            try:
                matched = plugin.match(event)
            except Exception:
                logger.opt(exception=True).error(
                    f"Plugin.match() raised an exception: {plugin.name}"
                )
                continue

            if not matched:
                continue

            try:
                consumed = await plugin.handle(event, bot)
            except Exception:
                logger.opt(exception=True).error(
                    f"Plugin.handle() raised an exception: {plugin.name}"
                )
                continue

            if consumed:
                return True

        return False
