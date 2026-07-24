"""PluginRuntime – central orchestrator wiring P1 discovery + P2 lifecycle.

``PluginRuntime.activate()`` discovers, imports, prepares, and starts
every plugin, then atomically publishes a :class:`RegistrySnapshot`.
Hot-reload follows the same pipeline with generation-based draining of
stale plugins.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.core.message_bus import MessageType
from src.plugin.definition import (
    PluginDefinition,
    collect_definition,
    extract_definition,
)
from src.plugin.lifecycle import LifecycleRunner
from src.plugin.loaded import LoadedPlugin, PluginState
from src.plugin.loader import discover_candidates
from src.plugin.registry import Registry, RegistrySnapshot, build_snapshot
from src.plugin.scope import ResourceScope

if TYPE_CHECKING:
    from src.core.bot import Bot
    from src.core.message_bus import MessageBus


# ---------------------------------------------------------------------------
# PluginRuntime
# ---------------------------------------------------------------------------


class PluginRuntime:
    """Orchestrates plugin activation, shutdown, and hot-reload.

    Wires together P1 discovery (``discover_candidates`` /
    ``resolve_candidates``) and P2 lifecycle (``LoadedPlugin`` state
    machine, ``ResourceScope``, ``LifecycleRunner``, ``Registry``).
    """

    def __init__(
        self,
        bus: MessageBus,
        bot: Bot,
        setup_timeout: float = 10.0,
        start_timeout: float = 10.0,
        stop_timeout: float = 10.0,
        task_shutdown_timeout: float = 5.0,
        drain_timeout: float = 10.0,
        max_consecutive_errors: int = 10,
    ) -> None:
        self._bus = bus
        self._bot = bot
        self.registry = Registry()
        self._lifecycle_runner = LifecycleRunner(
            setup_timeout=setup_timeout,
            start_timeout=start_timeout,
            stop_timeout=stop_timeout,
        )
        self._task_shutdown_timeout = task_shutdown_timeout
        self._drain_timeout = drain_timeout
        self._max_consecutive_errors = max_consecutive_errors
        self._plugins: dict[str, LoadedPlugin] = {}
        self._generation: int = 0
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def snapshot(self) -> RegistrySnapshot:
        """Shortcut to the current registry snapshot."""
        return self.registry.current

    @property
    def plugins(self) -> dict[str, LoadedPlugin]:
        """All managed plugins (including FAILED / STOPPED)."""
        return dict(self._plugins)

    @property
    def active_plugins(self) -> dict[str, LoadedPlugin]:
        """Only ACTIVE and DEGRADED plugins."""
        return {
            pid: p
            for pid, p in self._plugins.items()
            if p.state in (PluginState.ACTIVE, PluginState.DEGRADED)
        }

    @property
    def generation(self) -> int:
        return self._generation

    # ------------------------------------------------------------------
    # activation
    # ------------------------------------------------------------------

    async def activate(
        self,
        plugin_dirs: list[str],
        disabled: list[str] | None = None,
    ) -> int:
        """Full activation pipeline: discover → resolve → load → start → publish.

        Returns the number of ACTIVE plugins after activation.
        """
        if disabled is None:
            disabled = []

        self._generation += 1
        gen = self._generation
        logger.info(f"PluginRuntime activate gen={gen} starting")

        # 1. Discover candidates (P1 – no code execution).
        all_candidates = discover_candidates(plugin_dirs)

        # 2. Resolve SDK compatibility & dependency ordering (P1).
        from src.plugin import SDK_VERSION
        from src.plugin.topology import resolve_candidates

        loadable, skipped = resolve_candidates(all_candidates, SDK_VERSION)
        for c, reason in skipped:
            logger.warning(f"Plugin '{c.plugin_id}' skipped: {reason}")

        # 3. Filter disabled.
        if disabled:
            loadable = [c for c in loadable if c.plugin_id not in disabled]

        # 4. Register ACTION middleware pipeline (runs before legacy handlers).
        self._register_action_pipeline()

        # 5. For each candidate: create → import → prepare → start.
        for candidate in loadable:
            try:
                await self._activate_one(candidate, gen)
            except Exception:
                logger.opt(exception=True).error(
                    f"Unexpected error activating plugin '{candidate.plugin_id}'"
                )

        # 6. Build and publish snapshot.
        snapshot = build_snapshot(self._plugins, gen)
        old = self.registry.publish(snapshot)

        # 7. Drain plugins from the old generation.
        await self._drain_old_generation(old)

        active_count = sum(
            1 for p in self._plugins.values() if p.state == PluginState.ACTIVE
        )
        logger.info(
            f"PluginRuntime activate gen={gen} complete: "
            f"{active_count} active plugin(s)"
        )
        return active_count

    async def _activate_one(self, candidate, gen: int) -> None:
        """Run a single candidate through the full activation pipeline."""
        plugin_id = candidate.plugin_id

        # -- Step A: create LoadedPlugin --
        plugin = self._create_loaded_plugin(candidate, gen)
        if plugin is None:
            return

        # -- Step B: import module / build definition --
        ok = await self._import_plugin(plugin, candidate)
        if not ok:
            self._plugins[plugin_id] = plugin
            return

        # -- Step C: prepare (scope + bus registration) --
        ok = await self._prepare_plugin(plugin)
        if not ok:
            self._plugins[plugin_id] = plugin
            return

        # -- Step D: start (lifecycle hooks) --
        ok = await self._start_plugin(plugin)
        self._plugins[plugin_id] = plugin

    # ------------------------------------------------------------------
    # per-plugin pipeline steps
    # ------------------------------------------------------------------

    def _create_loaded_plugin(self, candidate, gen: int) -> LoadedPlugin | None:
        """Create a LoadedPlugin from a candidate, transition DISCOVERED→VALIDATED."""
        try:
            plugin = LoadedPlugin(
                manifest=candidate.manifest,
                definition=PluginDefinition(
                    plugin_id=candidate.plugin_id,
                    version=candidate.manifest.version,
                ),
                generation=gen,
                state=PluginState.DISCOVERED,
                loaded_at=time.time(),
            )
            plugin.transition(PluginState.VALIDATED, "candidate validated")
            return plugin
        except Exception as exc:
            logger.error(
                f"Failed to create LoadedPlugin for '{candidate.plugin_id}': {exc}"
            )
            return None

    async def _import_plugin(self, plugin: LoadedPlugin, candidate) -> bool:
        """Import plugin code and build its definition.  LOADED or FAILED."""
        return self._import_package_plugin(plugin, candidate)

    def _import_package_plugin(self, plugin: LoadedPlugin, candidate) -> bool:
        """Import a package plugin's entrypoint and build its definition."""
        from importlib import import_module

        from src.plugin.manager import _parse_entrypoint

        manifest = candidate.manifest
        module_name, attr_name = _parse_entrypoint(manifest.entrypoint)

        try:
            module = import_module(module_name)
        except Exception:
            logger.opt(exception=True).error(
                f"Failed to import plugin {manifest.id}: {module_name}"
            )
            plugin.set_failed(f"Import error: {module_name}")
            return False

        plugin.module = module
        obj = getattr(module, attr_name, None)
        if obj is None:
            msg = (
                f"Entrypoint attr '{attr_name}' not found in {module_name}"
            )
            logger.error(msg)
            plugin.set_failed(msg)
            return False

        # If obj is a new-style class with decorators, collect definition.
        if isinstance(obj, type) and hasattr(obj, "__plugin_id__"):
            collect_definition(obj)

        definition = extract_definition(obj, manifest.id, manifest.version)
        plugin.definition = definition

        # If obj is a class, instantiate it.
        if isinstance(obj, type):
            try:
                plugin.instance = obj()
            except Exception:
                logger.opt(exception=True).warning(
                    f"Could not instantiate plugin class for '{plugin.plugin_id}'"
                )

        try:
            plugin.transition(PluginState.LOADED, "package module imported")
        except Exception:
            logger.opt(exception=True).error(
                f"Failed to transition plugin '{plugin.plugin_id}' to LOADED"
            )
            plugin.set_failed("Transition to LOADED failed")
            return False

        return True

    async def _prepare_plugin(self, plugin: LoadedPlugin) -> bool:
        """Create ResourceScope, build PluginContext for package plugins,
        and register handlers on the bus.
        """
        try:
            scope = ResourceScope(
                plugin.plugin_id,
                plugin.generation,
                self._bus,
                task_shutdown_timeout=self._task_shutdown_timeout,
            )
            plugin.scope = scope
        except Exception:
            logger.opt(exception=True).error(
                f"Failed to create ResourceScope for '{plugin.plugin_id}'"
            )
            plugin.set_failed("ResourceScope creation failed")
            return False

        # Build PluginContext for package plugins (not legacy).
        if plugin.manifest is not None and bool(plugin.manifest.entrypoint):
            from src.plugin.context import (
                PluginConfigView,
                PluginContext,
                QQAgency,
            )

            permissions = plugin.manifest.permissions
            config_view = PluginConfigView(
                bot_id=self._bot.config.bot.qq,
                nickname=(
                    self._bot.config.bot.nickname[0]
                    if self._bot.config.bot.nickname
                    else str(self._bot.config.bot.qq)
                ),
                admin_users=tuple(self._bot.config.bot.admin_users),
                llm_enabled=self._bot.config.llm.enabled,
            )
            qq_agency = QQAgency(self._bot.api, permissions, plugin.plugin_id)

            plugin.context = PluginContext(
                plugin_id=plugin.plugin_id,
                version=plugin.version,
                generation=plugin.generation,
                permissions=permissions,
                api=qq_agency,
                config=config_view,
                _bus=self._bus,
                _bot=self._bot,
                _scope=plugin.scope,
            )

            # Load typed plugin config if manifest declares a schema.
            if plugin.manifest is not None and plugin.manifest.config is not None:
                from src.plugin.config import build_plugin_config, load_schema

                schema_ref = plugin.manifest.config.schema
                schema = load_schema(schema_ref)
                if schema is not None:
                    raw = self._bot.config.plugin.plugin_configs.get(
                        plugin.plugin_id, {}
                    )
                    try:
                        plugin.context.plugin_config = build_plugin_config(schema, raw)
                        logger.debug(
                            f"Plugin '{plugin.plugin_id}': config loaded from schema "
                            f"'{schema_ref}'"
                        )
                    except Exception:
                        logger.opt(exception=True).warning(
                            f"Plugin '{plugin.plugin_id}': failed to build config "
                            f"from schema '{schema_ref}', using raw dict"
                        )
                        plugin.context.plugin_config = raw
                elif schema_ref:
                    logger.warning(
                        f"Plugin '{plugin.plugin_id}': config schema "
                        f"'{schema_ref}' could not be loaded"
                    )

            # Wire PluginStorage if manifest declares storage permissions.
            if permissions.storage:
                from src.plugin.storage import PluginStorage

                try:
                    db_path = "data/messages.db"
                    storage = await PluginStorage.create(
                        plugin_id=plugin.plugin_id,
                        permissions=list(permissions.storage),
                        db_path=db_path,
                    )
                    plugin.context.storage = storage
                    plugin.scope.add_resource(storage)
                    logger.debug(
                        f"Plugin '{plugin.plugin_id}': storage wired"
                    )
                except Exception:
                    logger.opt(exception=True).warning(
                        f"Plugin '{plugin.plugin_id}': failed to create storage"
                    )

            # Wire SchedulerAgency if manifest permits scheduler.
            if permissions.scheduler:
                bot_scheduler = getattr(self._bot, "scheduler", None)
                if bot_scheduler is not None:
                    from src.plugin.scheduler_agency import SchedulerAgency

                    plugin.context.scheduler = SchedulerAgency(
                        bot_scheduler,
                        plugin.plugin_id,
                        permissions,
                        plugin.generation,
                    )
                    logger.debug(
                        f"Plugin '{plugin.plugin_id}': scheduler wired"
                    )

        try:
            self._register_on_bus(plugin)
        except Exception:
            logger.opt(exception=True).error(
                f"Failed to register '{plugin.plugin_id}' on bus"
            )
            plugin.set_failed("Bus registration failed")
            return False

        return True

    def _register_on_bus(self, plugin: LoadedPlugin) -> None:
        """Subscribe all handler specs from *plugin*'s definition on the bus."""
        if plugin.scope is None:
            return

        from src.plugin.bridge import (
            _CommandSpecAdapter,
            _EventSpecAdapter,
            _InternalSpecAdapter,
            _ObserverSpecAdapter,
        )

        d = plugin.definition
        ctx = plugin.context

        for cmd in d.commands:
            adapter = _CommandSpecAdapter(cmd, plugin_context=ctx, plugin_id=plugin.plugin_id)
            plugin.scope.subscribe(MessageType.EXTERNAL, adapter, cmd.priority)

        for eh in d.event_handlers:
            adapter = _EventSpecAdapter(eh, plugin_context=ctx, plugin_id=plugin.plugin_id)
            plugin.scope.subscribe(MessageType.EXTERNAL, adapter, eh.priority)

        for obs in d.observers:
            adapter = _ObserverSpecAdapter(obs, plugin_context=ctx, plugin_id=plugin.plugin_id)
            plugin.scope.subscribe(MessageType.EXTERNAL, adapter, 100)

        for ih in d.internal_handlers:
            adapter = _InternalSpecAdapter(ih, plugin_context=ctx, plugin_id=plugin.plugin_id)
            msg_type = (
                MessageType.INTERNAL
                if ih.message_type == "internal"
                else MessageType.LIFECYCLE
            )
            plugin.scope.subscribe(msg_type, adapter, 0)

    async def _start_plugin(self, plugin: LoadedPlugin) -> bool:
        """Run lifecycle hooks (setup → start), transition to ACTIVE or FAILED."""
        try:
            plugin.transition(PluginState.STARTING, "begin lifecycle hooks")
        except Exception:
            # May already be FAILED from earlier step.
            if plugin.state == PluginState.FAILED:
                return False
            raise

        result = await self._lifecycle_runner.setup_and_start(plugin, self._bot)

        if result.success:
            try:
                plugin.transition(PluginState.ACTIVE, "lifecycle hooks completed")
            except Exception:
                logger.opt(exception=True).error(
                    f"Failed to transition '{plugin.plugin_id}' to ACTIVE"
                )
                # Plugin was set to FAILED by setup_and_start compensation.
                return False
            return True

        # setup_and_start already called set_failed + stop compensation.
        if plugin.scope is not None:
            await plugin.scope.close()
        return False

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop all plugins and publish an empty snapshot."""
        logger.info("PluginRuntime shutdown starting")
        await self._drain_all()
        self.registry.publish(RegistrySnapshot(generation=0))
        logger.info("PluginRuntime shutdown complete")

    async def reload(
        self,
        plugin_dirs: list[str],
        disabled: list[str] | None = None,
    ) -> int:
        """Hot-reload: same pipeline as :meth:`activate`.

        Old generations are drained inside ``activate()`` via
        ``_drain_old_generation()``.
        """
        # Clear module cache so re-imports pick up current source.
        self._clear_plugin_module_cache(plugin_dirs)
        importlib.invalidate_caches()
        return await self.activate(plugin_dirs, disabled)

    # ------------------------------------------------------------------
    # drain
    # ------------------------------------------------------------------

    async def _drain_old_generation(self, old_snapshot: RegistrySnapshot) -> None:
        """Stop plugins that were active in the old snapshot but not in the new."""
        new_ids = self.registry.current.plugin_ids
        stale_ids = old_snapshot.plugin_ids - new_ids

        if not stale_ids:
            return

        logger.info(
            f"Draining {len(stale_ids)} stale plugin(s) from gen={old_snapshot.generation}"
        )

        for pid in stale_ids:
            if pid in self._plugins:
                await self._stop_plugin(self._plugins[pid])

        # Brief wait for in-flight handlers.
        await asyncio.sleep(min(self._drain_timeout, 1.0))

    async def _drain_all(self) -> None:
        """Stop every plugin in reverse load order."""
        for pid in reversed(list(self._plugins.keys())):
            await self._stop_plugin(self._plugins[pid])

    async def _stop_plugin(self, plugin: LoadedPlugin) -> None:
        """Stop a single plugin: stop hooks → STOPPING → STOPPED → close scope."""
        if plugin.state in (PluginState.STOPPED,):
            return

        logger.info(f"Stopping plugin '{plugin.plugin_id}' (state={plugin.state.value})")

        # Run stop hooks (always, for compensation).
        if plugin.state in (
            PluginState.LOADED,
            PluginState.STARTING,
            PluginState.ACTIVE,
            PluginState.DEGRADED,
            PluginState.FAILED,
        ):
            await self._lifecycle_runner.stop(plugin, self._bot)

        # Transition to STOPPING → STOPPED.
        with suppress(Exception):
            if plugin.state != PluginState.STOPPING:
                plugin.transition(PluginState.STOPPING, "shutdown initiated")

        with suppress(Exception):
            plugin.transition(PluginState.STOPPED, "shutdown complete")

        # Close resource scope.
        if plugin.scope is not None:
            await plugin.scope.close()

    # ------------------------------------------------------------------
    # health tracking
    # ------------------------------------------------------------------

    def record_handler_success(self, plugin_id: str, elapsed_ms: float = 0.0) -> None:
        """Record a successful handler invocation for health tracking."""
        p = self._plugins.get(plugin_id)
        if p is None:
            return
        p.health.record_success(elapsed_ms)
        if (
            p.state == PluginState.DEGRADED
            and p.health.consecutive_errors == 0
        ):
            with suppress(Exception):
                p.transition(PluginState.ACTIVE, "health restored")

    def record_handler_error(self, plugin_id: str, error: str) -> None:
        """Record a handler error; degrade if consecutive errors exceed threshold."""
        p = self._plugins.get(plugin_id)
        if p is None:
            return
        p.health.record_error(error)
        if (
            p.state == PluginState.ACTIVE
            and p.health.consecutive_errors > self._max_consecutive_errors
        ):
            with suppress(Exception):
                p.transition(
                    PluginState.DEGRADED,
                    f"consecutive errors {p.health.consecutive_errors} > {self._max_consecutive_errors}",
                )

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------

    def status_summary(self) -> list[dict]:
        """Return status summary dicts for all managed plugins."""
        return [p.status_summary() for p in self._plugins.values()]

    def get_plugin(self, plugin_id: str) -> LoadedPlugin | None:
        """Look up a managed plugin by id."""
        return self._plugins.get(plugin_id)

    # ------------------------------------------------------------------
    # ACTION middleware pipeline
    # ------------------------------------------------------------------

    def _register_action_pipeline(self) -> None:
        """Build and register the ACTION middleware pipeline on the bus.

        Registered at priority 0 so it runs before other ACTION handlers.
        """
        if not hasattr(self._bot, "config") or not hasattr(self._bot, "api"):
            logger.debug("Action pipeline skipped: bot missing config or api")
            return

        try:
            from src.plugin.bridge import _MiddlewarePipelineAdapter

            pipeline = self._build_action_pipeline()
            adapter = _MiddlewarePipelineAdapter(pipeline)
            self._bus.subscribe(MessageType.ACTION, adapter, 0)
            logger.debug("Action middleware pipeline registered at priority 0")
        except Exception:
            logger.opt(exception=True).warning(
                "Failed to register ACTION middleware pipeline"
            )

    def _build_action_pipeline(self):
        """Build the middleware pipeline for ACTION messages."""
        from plugins.action_queue import ActionQueueMiddleware
        from src.plugin.middleware import MiddlewarePipeline

        action_queue_mw = ActionQueueMiddleware(config=self._bot.config.action_queue)
        terminal = self._make_action_terminal()

        return MiddlewarePipeline(
            middlewares=[action_queue_mw],
            terminal=terminal,
        )

    def _make_action_terminal(self):
        """Create the terminal executor for ACTION messages."""

        async def terminal(action: str, params: dict) -> dict:
            return await self._bot.api._raw_call(action, **params)

        return terminal

    # ------------------------------------------------------------------
    # per-plugin reload
    # ------------------------------------------------------------------

    async def reload_plugin(
        self,
        plugin_id: str,
        plugin_dirs: list[str],
        disabled: list[str] | None = None,
    ) -> bool:
        """Targeted hot-reload of a single plugin by id.

        1. Discover the candidate from *plugin_dirs*.
        2. If not found or disabled: stop old instance, remove from snapshot.
        3. Increment generation, clear only that plugin's module cache.
        4. Shadow-load the new version via ``_activate_one``.
        5. Build new snapshot, publish atomically, drain old generation.
        6. On failure, restore the old instance.

        Returns ``True`` if the plugin ended up ACTIVE.
        """
        if disabled is None:
            disabled = []

        # 1. Find candidate for this specific plugin_id.
        all_candidates = discover_candidates(plugin_dirs)
        candidate = next(
            (c for c in all_candidates if c.plugin_id == plugin_id), None
        )

        old = self._plugins.get(plugin_id)
        old_snapshot = self.registry.current

        # 2. Not found or disabled — stop and remove.
        if candidate is None or plugin_id in disabled:
            self._generation += 1
            if old is not None:
                del self._plugins[plugin_id]
                snapshot = build_snapshot(self._plugins, self._generation)
                self.registry.publish(snapshot)
                await self._stop_plugin(old)
            return False

        # 3. Increment generation, clear per-plugin module cache.
        self._generation += 1
        gen = self._generation
        self._clear_single_plugin_module_cache(plugin_id, plugin_dirs)

        # 4. Activate the candidate (shadow-loads new version).
        try:
            await self._activate_one(candidate, gen)
            new = self._plugins.get(plugin_id)
            if new is None or new.state != PluginState.ACTIVE:
                raise RuntimeError(
                    f"plugin '{plugin_id}' did not reach ACTIVE state"
                )
        except Exception:
            logger.opt(exception=True).error(
                f"reload_plugin '{plugin_id}': activation failed, restoring old instance"
            )
            failed = self._plugins.get(plugin_id)
            if failed is not None and failed is not old:
                await self._stop_plugin(failed)
            if old is not None:
                self._plugins[plugin_id] = old
            elif plugin_id in self._plugins:
                del self._plugins[plugin_id]
            self.registry.publish(old_snapshot)
            return False

        # 5. Build new snapshot (all active plugins including the reloaded one).
        snapshot = build_snapshot(self._plugins, gen)
        self.registry.publish(snapshot)

        # 6. Drain the replaced object directly. Looking it up through
        # self._plugins would stop the new instance instead.
        if old is not None and old is not self._plugins.get(plugin_id):
            await self._stop_plugin(old)
            await asyncio.sleep(min(self._drain_timeout, 1.0))

        # 7. Report success.
        new = self._plugins.get(plugin_id)
        return new is not None and new.state == PluginState.ACTIVE

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _clear_plugin_module_cache(self, plugin_dirs: list[str]) -> None:
        """Remove cached plugin modules from ``sys.modules``."""
        for dir_name in plugin_dirs:
            path = Path(dir_name).resolve()
            if not path.is_dir():
                continue
            pkg_name = path.name

            to_remove = [
                name
                for name in sys.modules
                if name == pkg_name or name.startswith(pkg_name + ".")
            ]
            for name in to_remove:
                del sys.modules[name]
                logger.debug(f"Evicted cached module: {name}")

            pycache = path / "__pycache__"
            if pycache.is_dir():
                import shutil

                shutil.rmtree(pycache)
                logger.debug(f"Purged pycache: {pycache}")

    def _clear_single_plugin_module_cache(
        self, plugin_id: str, plugin_dirs: list[str]
    ) -> None:
        """Remove cached modules for a single plugin from ``sys.modules``."""
        import shutil

        for dir_name in plugin_dirs:
            path = Path(dir_name).resolve()
            if not path.is_dir():
                continue
            pkg_name = path.name
            prefix = f"{pkg_name}.{plugin_id}"

            to_remove = [
                name
                for name in sys.modules
                if name == prefix or name.startswith(prefix + ".")
            ]
            for name in to_remove:
                del sys.modules[name]
                logger.debug(f"Evicted cached module: {name}")

            pycache = path / plugin_id / "__pycache__"
            if pycache.is_dir():
                shutil.rmtree(pycache)
                logger.debug(f"Purged pycache: {pycache}")
