"""Plugin registry, auto-discovery, and bus-based event dispatch.

In Phase 2 the ``PluginManager`` no longer manages its own dispatch
loop.  Instead, discovered plugins are registered as subscribers on
the :class:`MessageBus`, and the bus handles priority ordering and
suppression.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.core.message_bus import MessageType
from src.plugin.base import Plugin

if TYPE_CHECKING:
    from src.core.bot import Bot
    from src.core.message_bus import MessageBus
    from src.plugin.base import Event


class PluginManager:
    """Manages plugin lifecycle: discovery and bus registration.

    Plugins are discovered from Python modules and subscribed to the
    :class:`MessageBus` as ``EXTERNAL`` handlers.  Dispatch is handled
    entirely by the bus.
    """

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
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
        """Register a plugin on the message bus as an EXTERNAL handler."""
        self._plugins.append(plugin)
        self._bus.subscribe(MessageType.EXTERNAL, plugin, plugin.priority)
        logger.debug(f"Plugin registered: {plugin.name} (priority={plugin.priority})")

    def unregister(self, plugin_name: str) -> bool:
        """Remove a plugin by name from both local list and the bus."""
        before = len(self._plugins)
        self._plugins = [p for p in self._plugins if p.name != plugin_name]
        removed = before != len(self._plugins)
        if removed:
            self._bus.unsubscribe(MessageType.EXTERNAL, plugin_name)
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
            # New-style: @subscribe decorator
            subscribe_info = getattr(obj, "__subscribe__", None)
            if subscribe_info is not None:
                msg_type, priority = subscribe_info
                self._register_subscribe_handler(obj, msg_type, priority, disabled)
                continue

            # Legacy-style: @command / @on_regex / @on_keyword / @on_notice
            plugin = getattr(obj, "__plugin__", None)
            if plugin is None:
                continue
            if not isinstance(plugin, Plugin):
                continue
            if plugin.name in disabled:
                logger.debug(f"Skipping disabled plugin: {plugin.name}")
                continue
            self.register(plugin)

        # Class-based plugins (e.g. RepeaterPlugin)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is Plugin or cls.__module__ != module.__name__:
                continue
            if not (hasattr(cls, "name") and hasattr(cls, "priority")
                    and hasattr(cls, "match") and hasattr(cls, "handle")):
                continue
            instance = cls()
            if not isinstance(instance, Plugin):
                continue
            if instance.name in disabled:
                logger.debug(f"Skipping disabled plugin: {instance.name}")
                continue
            self.register(instance)

    def _register_subscribe_handler(
        self,
        func,
        msg_type: MessageType,
        priority: int,
        disabled: list[str],
    ) -> None:
        """Create a Plugin wrapper for a @subscribe handler and register it."""
        name = func.__name__
        if name in disabled:
            logger.debug(f"Skipping disabled subscribe handler: {name}")
            return

        handler = _SubscribeAdapter(func, name, priority)
        self._plugins.append(handler)
        self._bus.subscribe(msg_type, handler, priority)
        logger.debug(f"Subscribe handler registered: {name} → {msg_type.value} (priority={priority})")

    # ------------------------------------------------------------------
    # dispatch (deprecated – bus handles this)
    # ------------------------------------------------------------------

    async def dispatch(self, event: Event, bot: Bot) -> bool:
        """Route *event* through the message bus.

        .. deprecated::
           Call ``bot.message_bus.dispatch()`` directly instead.
           This method exists for backward compatibility in tests.
        """
        from src.core.message_bus import BusMessage

        msg = BusMessage(
            type=MessageType.EXTERNAL,
            payload=event,
            source="event_bus",
        )
        result = await self._bus.dispatch(msg, bot)
        return bool(result)


# ------------------------------------------------------------------
# adapter: wraps a @subscribe function as a Plugin
# ------------------------------------------------------------------


class _SubscribeAdapter:
    """Adapts a ``@subscribe`` function to the :class:`Plugin` protocol."""

    def __init__(self, func, name: str, priority: int) -> None:
        self._func = func
        self.name = name
        self.priority = priority

    def match(self, payload) -> bool:
        # @subscribe handlers always match their message type;
        # content filtering is done inside handle().
        return True

    async def handle(self, payload, bot) -> bool:
        return await self._func(payload, bot)
