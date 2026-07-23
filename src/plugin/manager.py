"""Plugin registry, auto-discovery, and bus-based event dispatch.

In Phase 2 the ``PluginManager`` no longer manages its own dispatch
loop.  Instead, discovered plugins are registered as subscribers on
the :class:`MessageBus`, and the bus handles priority ordering and
suppression.

P1 discovery uses :mod:`src.plugin.loader` for code-free discovery
before importing and registering plugins.  Only package plugins are
supported (legacy .py plugins removed in P6).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.core.message_bus import MessageType
from src.plugin.loader import discover_candidates

if TYPE_CHECKING:
    from src.core.message_bus import MessageBus, SubscriptionScope
    from src.plugin.runtime import PluginRuntime


class PluginManager:
    """Manages plugin lifecycle: discovery and bus registration.

    Plugins are discovered from package directories and subscribed to the
    :class:`MessageBus` as handlers.  Dispatch is handled entirely by the bus.

    Subscriptions are tracked via a :class:`SubscriptionScope` so that
    calling :meth:`close` removes all of them at once, enabling clean
    hot-reload without subscription residue.
    """

    def __init__(
        self, bus: MessageBus, runtime: PluginRuntime | None = None
    ) -> None:
        self._bus = bus
        self._plugins: list[object] = []
        self._scope: SubscriptionScope | None = None
        self._generation: int = 0
        self._runtime = runtime

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def plugins(self) -> list[object]:
        """Return a shallow copy of the registered plugin list."""
        return list(self._plugins)

    @property
    def plugin_count(self) -> int:
        """Number of registered plugins."""
        return len(self._plugins)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Remove all subscriptions created by this manager.  Idempotent.

        When a :class:`PluginRuntime` is provided, the caller should
        ``await runtime.shutdown()`` **before** calling this method
        (the bot's :meth:`_cleanup` already does this).
        """
        if self._scope is not None:
            self._scope.close()
            self._scope = None
        self._plugins.clear()
        logger.debug("PluginManager closed: all subscriptions removed")

    # ------------------------------------------------------------------
    # auto-discovery
    # ------------------------------------------------------------------

    def auto_discover(
        self,
        plugin_dirs: list[str],
        disabled: list[str] | None = None,
    ) -> int:
        """Scan *plugin_dirs* for package plugins and register them.

        If a :class:`PluginRuntime` was provided, delegates to
        ``runtime.activate()``.  Otherwise falls back to the
        traditional inline discovery path (for tests without Runtime).
        """
        if disabled is None:
            disabled = []

        # Delegate to PluginRuntime when available (P2 path).
        if self._runtime is not None:
            import asyncio

            return asyncio.get_event_loop().run_until_complete(
                self._runtime.activate(plugin_dirs, disabled)
            )

        # Close old scope to remove all previous subscriptions.
        if self._scope is not None:
            self._scope.close()

        # Clear previously registered plugins and module cache.
        self._plugins.clear()
        self._clear_plugin_module_cache(plugin_dirs)
        importlib.invalidate_caches()

        # Create a fresh scope for this discovery run.
        self._generation += 1
        self._scope = self._bus.create_scope(
            "plugin_manager", generation=self._generation
        )

        # P1: discover candidates without executing plugin code.
        all_candidates = discover_candidates(plugin_dirs)

        # P1: resolve SDK compatibility and dependency ordering.
        from src.plugin import SDK_VERSION
        from src.plugin.topology import resolve_candidates

        loadable, skipped = resolve_candidates(all_candidates, SDK_VERSION)
        for c, reason in skipped:
            logger.warning(f"Plugin '{c.plugin_id}' skipped: {reason}")

        # Filter disabled plugin_ids.
        if disabled:
            loadable = [
                c for c in loadable if c.plugin_id not in disabled
            ]

        count_before = self.plugin_count

        for candidate in loadable:
            self._load_package_candidate(candidate)

        newly_registered = self.plugin_count - count_before
        logger.info(
            f"Plugin auto-discovery complete: {newly_registered} plugin(s) loaded "
            f"(total: {self.plugin_count})"
        )
        return newly_registered

    def _clear_plugin_module_cache(self, plugin_dirs: list[str]) -> None:
        """Remove cached plugin modules from ``sys.modules`` and purge
        ``__pycache__`` so that re-imports see current source.
        """
        for dir_name in plugin_dirs:
            path = Path(dir_name).resolve()
            if not path.is_dir():
                continue
            pkg_name = path.name

            # Clear the package itself and all its submodules.
            to_remove = [
                name
                for name in sys.modules
                if name == pkg_name or name.startswith(pkg_name + ".")
            ]
            for name in to_remove:
                del sys.modules[name]
                logger.debug(f"Evicted cached module: {name}")

            # Purge __pycache__ to force recompilation.
            pycache = path / "__pycache__"
            if pycache.is_dir():
                import shutil

                shutil.rmtree(pycache)
                logger.debug(f"Purged pycache: {pycache}")

    # ------------------------------------------------------------------
    # P1: package plugin loading
    # ------------------------------------------------------------------

    def _load_package_candidate(self, candidate) -> None:
        """Import a package plugin's entrypoint and register its handlers."""
        from importlib import import_module

        from src.plugin.definition import collect_definition, extract_definition

        manifest = candidate.manifest
        module_name, attr_name = _parse_entrypoint(manifest.entrypoint)

        try:
            module = import_module(module_name)
        except Exception:
            logger.opt(exception=True).error(
                f"Failed to import plugin {manifest.id}: {module_name}"
            )
            return

        obj = getattr(module, attr_name, None)
        if obj is None:
            logger.error(
                f"Entrypoint attr '{attr_name}' not found in {module_name} "
                f"for plugin {manifest.id}"
            )
            return

        # If obj is a new-style class, collect its definition.
        if isinstance(obj, type) and hasattr(obj, "__plugin_id__"):
            collect_definition(obj)

        definition = extract_definition(obj, manifest.id, manifest.version)
        self._register_definition(definition, manifest)

    def _register_definition(self, definition, manifest=None) -> None:
        """Register all handlers from a :class:`PluginDefinition` on the bus."""

        # Command handlers → EXTERNAL consumer adapters.
        for cmd in definition.commands:
            adapter = _CommandSpecAdapter(cmd)
            self._plugins.append(adapter)
            self._bus.subscribe(
                MessageType.EXTERNAL, adapter, cmd.priority, scope=self._scope
            )

        # Event handlers → EXTERNAL consumer adapters.
        for eh in definition.event_handlers:
            adapter = _EventSpecAdapter(eh)
            self._plugins.append(adapter)
            self._bus.subscribe(
                MessageType.EXTERNAL, adapter, eh.priority, scope=self._scope
            )

        # Observers → EXTERNAL (non-consuming; always returns False).
        for obs in definition.observers:
            adapter = _ObserverSpecAdapter(obs)
            self._plugins.append(adapter)
            self._bus.subscribe(
                MessageType.EXTERNAL, adapter, 100, scope=self._scope
            )

        # Internal handlers → INTERNAL.
        for ih in definition.internal_handlers:
            adapter = _InternalSpecAdapter(ih)
            self._plugins.append(adapter)
            msg_type = (
                MessageType.INTERNAL
                if ih.message_type == "internal"
                else MessageType.LIFECYCLE
            )
            self._bus.subscribe(msg_type, adapter, 0, scope=self._scope)

        plugin_id = definition.plugin_id
        logger.info(
            f"Plugin '{plugin_id}' v{definition.version} registered: "
            f"{len(definition.commands)} cmd(s), "
            f"{len(definition.event_handlers)} handler(s), "
            f"{len(definition.observers)} observer(s), "
            f"{len(definition.internal_handlers)} internal, "
            f"{len(definition.lifecycle_hooks)} lifecycle"
        )

# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

from src.plugin.bridge import (  # noqa: E402
    _CommandSpecAdapter,
    _EventSpecAdapter,
    _InternalSpecAdapter,
    _ObserverSpecAdapter,
)


def _parse_entrypoint(entrypoint: str) -> tuple[str, str]:
    """Parse ``"module.path:attr"`` into ``(module_name, attr_name)``."""
    import re

    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_.]*):([a-zA-Z_][a-zA-Z0-9_]*)$", entrypoint)
    if m is None:
        raise ValueError(f"Invalid entrypoint format: {entrypoint}")
    return m.group(1), m.group(2)
