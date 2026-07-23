"""LifecycleRunner – executes setup/start/stop hooks with timeout and compensation.

Each plugin's :class:`PluginDefinition` may declare lifecycle hooks.
:class:`LifecycleRunner` runs them in order, enforcing per-phase timeouts,
best-effort completion (remaining hooks still run after one fails), and
compensation semantics (stop hooks run if setup/start fails).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.core.bot import Bot
    from src.plugin.definition import LifecycleHookSpec
    from src.plugin.loaded import LoadedPlugin


# ---------------------------------------------------------------------------
# result types
# ---------------------------------------------------------------------------


@dataclass
class LifecycleHookResult:
    """Result of executing a single lifecycle hook."""

    hook_type: str
    success: bool
    elapsed_ms: float
    error: str = ""


@dataclass
class LifecycleResult:
    """Aggregated result for a phase (setup / start / stop)."""

    phase: str
    success: bool
    results: list[LifecycleHookResult] = field(default_factory=list)
    total_elapsed_ms: float = 0.0
    timeout: bool = False

    @property
    def failed_hooks(self) -> list[LifecycleHookResult]:
        return [r for r in self.results if not r.success]


# ---------------------------------------------------------------------------
# LifecycleRunner
# ---------------------------------------------------------------------------


class LifecycleRunner:
    """Executes :class:`LifecycleHookSpec` handlers from a plugin definition.

    Hooks run sequentially within each phase.  Each hook is guarded by
    ``asyncio.wait_for``.  Failures are recorded but do **not** prevent
    remaining hooks from executing.
    """

    def __init__(
        self,
        setup_timeout: float = 10.0,
        start_timeout: float = 10.0,
        stop_timeout: float = 10.0,
    ) -> None:
        self._timeouts: dict[str, float] = {
            "setup": setup_timeout,
            "start": start_timeout,
            "stop": stop_timeout,
        }

    # ------------------------------------------------------------------
    # hook filtering
    # ------------------------------------------------------------------

    @staticmethod
    def get_hooks(
        plugin: LoadedPlugin, hook_type: str
    ) -> list[LifecycleHookSpec]:
        """Return lifecycle hooks of *hook_type* from *plugin*'s definition."""
        return [
            h
            for h in plugin.definition.lifecycle_hooks
            if h.hook_type == hook_type
        ]

    # ------------------------------------------------------------------
    # phase execution
    # ------------------------------------------------------------------

    async def run_phase(
        self,
        plugin: LoadedPlugin,
        hook_type: str,
        bot: Bot,
    ) -> LifecycleResult:
        """Run all hooks of one type sequentially.

        Each hook is guarded by a per-phase timeout.  Failures are
        recorded; remaining hooks still execute.
        """
        hooks = self.get_hooks(plugin, hook_type)
        result = LifecycleResult(phase=hook_type, success=True)
        t0 = time.time()

        if not hooks:
            result.total_elapsed_ms = (time.time() - t0) * 1000
            return result

        phase_timeout = self._timeouts.get(hook_type, 10.0)

        for hook in hooks:
            hook_result = await self._run_single_hook(
                plugin, hook, phase_timeout, bot
            )
            result.results.append(hook_result)
            if not hook_result.success:
                result.success = False

        result.total_elapsed_ms = (time.time() - t0) * 1000
        return result

    async def setup_and_start(
        self, plugin: LoadedPlugin, bot: Bot
    ) -> LifecycleResult:
        """Run setup hooks, then start hooks.

        If *setup* fails: calls ``plugin.set_failed()``, runs stop hooks
        as compensation, returns failure result.

        If *start* fails: same compensation (set_failed + stop hooks).
        """
        # -- setup phase --
        setup_result = await self.run_phase(plugin, "setup", bot)
        if not setup_result.success:
            plugin.set_failed(f"Setup hooks failed for '{plugin.plugin_id}'")
            # Compensation: run stop hooks.
            await self.run_phase(plugin, "stop", bot)
            return LifecycleResult(
                phase="setup_and_start",
                success=False,
                results=setup_result.results,
                total_elapsed_ms=setup_result.total_elapsed_ms,
            )

        # -- start phase --
        start_result = await self.run_phase(plugin, "start", bot)
        if not start_result.success:
            plugin.set_failed(f"Start hooks failed for '{plugin.plugin_id}'")
            await self.run_phase(plugin, "stop", bot)
            return LifecycleResult(
                phase="setup_and_start",
                success=False,
                results=setup_result.results + start_result.results,
                total_elapsed_ms=(
                    setup_result.total_elapsed_ms + start_result.total_elapsed_ms
                ),
            )

        return LifecycleResult(
            phase="setup_and_start",
            success=True,
            results=setup_result.results + start_result.results,
            total_elapsed_ms=(
                setup_result.total_elapsed_ms + start_result.total_elapsed_ms
            ),
        )

    async def stop(self, plugin: LoadedPlugin, bot: Bot) -> LifecycleResult:
        """Run stop hooks.  Errors are logged but NOT fatal.

        Always returns ``success=True`` for the phase itself (we're
        shutting down regardless).
        """
        result = await self.run_phase(plugin, "stop", bot)
        failed = result.failed_hooks
        if failed:
            logger.warning(
                f"Plugin '{plugin.plugin_id}': {len(failed)} stop hook(s) failed"
            )
        # Phase always "succeeds" during shutdown.
        return LifecycleResult(
            phase="stop",
            success=True,
            results=result.results,
            total_elapsed_ms=result.total_elapsed_ms,
        )

    # ------------------------------------------------------------------
    # single hook execution
    # ------------------------------------------------------------------

    async def _run_single_hook(
        self,
        plugin: LoadedPlugin,
        hook: LifecycleHookSpec,
        timeout: float,
        bot: Bot,
    ) -> LifecycleHookResult:
        """Run one lifecycle hook with ``asyncio.wait_for``.

        Supports both sync and async handlers.  ``CancelledError`` is
        re-raised (not caught as a general Exception).
        """
        handler = hook.handler
        arg = plugin.context
        t0 = time.time()

        try:
            if inspect.iscoroutinefunction(handler):
                coro = handler(arg)
                await asyncio.wait_for(coro, timeout=timeout)
            else:
                # Sync handler – run in thread to avoid blocking the event loop.
                loop = asyncio.get_running_loop()
                await asyncio.wait_for(
                    loop.run_in_executor(None, handler, arg),
                    timeout=timeout,
                )
        except TimeoutError:
            elapsed = (time.time() - t0) * 1000
            logger.error(
                f"Plugin '{plugin.plugin_id}': {hook.hook_type} hook timed out "
                f"after {elapsed:.0f}ms"
            )
            return LifecycleHookResult(
                hook_type=hook.hook_type,
                success=False,
                elapsed_ms=elapsed,
                error=f"Timeout after {timeout}s",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            elapsed = (time.time() - t0) * 1000
            logger.opt(exception=True).error(
                f"Plugin '{plugin.plugin_id}': {hook.hook_type} hook failed"
            )
            return LifecycleHookResult(
                hook_type=hook.hook_type,
                success=False,
                elapsed_ms=elapsed,
                error=str(exc),
            )

        elapsed = (time.time() - t0) * 1000
        return LifecycleHookResult(
            hook_type=hook.hook_type,
            success=True,
            elapsed_ms=elapsed,
        )
