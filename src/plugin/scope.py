"""ResourceScope & TaskSupervisor – per-plugin, per-generation resource lifecycle.

Every :class:`LoadedPlugin` gets a :class:`ResourceScope` that bundles
bus subscriptions, background tasks, and generic resources.  Closing the
scope tears everything down with failure compensation.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from src.core.message_bus import MessageBus, MessageType, SubscriptionScope


# ---------------------------------------------------------------------------
# TaskInfo
# ---------------------------------------------------------------------------


@dataclass
class TaskInfo:
    """Metadata for a background task managed by :class:`TaskSupervisor`."""

    name: str
    task: asyncio.Task | None = None
    status: str = "pending"  # pending / running / cancelled / done / failed
    plugin_id: str = ""


# ---------------------------------------------------------------------------
# TaskSupervisor
# ---------------------------------------------------------------------------


class TaskSupervisor:
    """Manages async background tasks for a single plugin.

    Every task created through the supervisor is tracked and will be
    cancelled during :meth:`shutdown`.
    """

    def __init__(self, plugin_id: str, shutdown_timeout: float = 5.0) -> None:
        self.plugin_id = plugin_id
        self.shutdown_timeout = shutdown_timeout
        self._tasks: dict[str, TaskInfo] = {}
        self._closed: bool = False

    # -- properties --------------------------------------------------------

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    @property
    def active_task_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == "running")

    @property
    def closed(self) -> bool:
        return self._closed

    # -- task creation -----------------------------------------------------

    def create_task(self, name: str, coro: Coroutine) -> asyncio.Task:
        """Wrap *coro* in an :class:`asyncio.Task` and track it.

        Raises ``RuntimeError`` if the supervisor is already closed.
        """
        if self._closed:
            raise RuntimeError(
                f"TaskSupervisor for '{self.plugin_id}' is closed"
            )

        info = TaskInfo(name=name, plugin_id=self.plugin_id)

        async def _runner() -> Any:
            info.status = "running"
            try:
                return await coro
            except asyncio.CancelledError:
                info.status = "cancelled"
                raise
            except Exception:
                info.status = "failed"
                raise
            finally:
                if info.status == "running":
                    info.status = "done"

        task = asyncio.create_task(_runner())
        info.task = task
        self._tasks[name] = info

        def _done_callback(t: asyncio.Task) -> None:
            if info.status == "running":
                if t.cancelled():
                    info.status = "cancelled"
                elif t.exception() is not None:
                    info.status = "failed"
                else:
                    info.status = "done"

        task.add_done_callback(_done_callback)
        return task

    # -- shutdown ----------------------------------------------------------

    async def shutdown(self) -> list[str]:
        """Cancel all running tasks and wait for them to finish.

        Returns names of tasks that did **not** complete cooperatively
        within ``shutdown_timeout`` seconds.

        Idempotent – subsequent calls are no-ops.
        """
        if self._closed:
            return []

        self._closed = True
        uncancelled: list[str] = []

        # Cancel all tasks that are still running / pending.
        for info in self._tasks.values():
            if info.task is not None and not info.task.done():
                info.task.cancel()

        # Wait for them to finish (cooperatively).
        if self._tasks:
            tasks = [
                t.task for t in self._tasks.values() if t.task is not None
            ]
            done, pending = await asyncio.wait(
                tasks,
                timeout=self.shutdown_timeout,
                return_when=asyncio.ALL_COMPLETED,
            )
            for t in pending:
                # Find the name
                for name, info in self._tasks.items():
                    if info.task is t:
                        uncancelled.append(name)
                        break
                t.cancel()

        return uncancelled

    # -- diagnostics -------------------------------------------------------

    def status_summary(self) -> dict:
        return {
            "plugin_id": self.plugin_id,
            "task_count": self.task_count,
            "active_task_count": self.active_task_count,
            "closed": self._closed,
            "tasks": {
                name: info.status for name, info in self._tasks.items()
            },
        }


# ---------------------------------------------------------------------------
# ResourceScope
# ---------------------------------------------------------------------------


class ResourceScope:
    """Bundled subscriptions + tasks + generic resources per plugin per generation.

    Three-step teardown on :meth:`close`:
    1. Bulk-unsubscribe from the bus.
    2. Cancel & drain background tasks.
    3. Close / cancel generic resources.

    Every step executes even if a previous step fails (failure compensation).
    """

    def __init__(
        self,
        plugin_id: str,
        generation: int,
        bus: MessageBus,
        task_shutdown_timeout: float = 5.0,
    ) -> None:
        self.plugin_id = plugin_id
        self.generation = generation
        self._bus = bus
        self._subscription_scope: SubscriptionScope = bus.create_scope(
            plugin_id, generation
        )
        self._task_supervisor = TaskSupervisor(plugin_id, task_shutdown_timeout)
        self._resources: list[Any] = []
        self._closed: bool = False
        self._close_errors: list[str] = []

    # -- properties --------------------------------------------------------

    @property
    def subscription_scope(self):
        return self._subscription_scope

    @property
    def task_supervisor(self) -> TaskSupervisor:
        return self._task_supervisor

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def close_errors(self) -> list[str]:
        return list(self._close_errors)

    # -- resource tracking -------------------------------------------------

    def subscribe(
        self,
        message_type: MessageType,
        handler: Any,
        priority: int,
    ) -> None:
        """Subscribe *handler* on the bus, scoped to this plugin generation."""
        self._bus.subscribe(
            message_type, handler, priority, scope=self._subscription_scope
        )

    def create_task(self, name: str, coro: Coroutine) -> asyncio.Task:
        """Create a tracked background task."""
        return self._task_supervisor.create_task(name, coro)

    def add_resource(self, resource: Any) -> None:
        """Track a generic resource for cleanup during :meth:`close`.

        The resource should have a ``close()`` or ``cancel()`` method.
        """
        self._resources.append(resource)

    # -- teardown ----------------------------------------------------------

    async def close(self) -> list[str]:
        """Tear down all resources.  Idempotent; returns collected errors."""
        if self._closed:
            return self._close_errors

        self._closed = True

        # Step 1: close subscriptions.
        try:
            self._subscription_scope.close()
        except Exception as exc:
            msg = f"SubscriptionScope close error: {exc}"
            logger.error(msg)
            self._close_errors.append(msg)

        # Step 2: shutdown tasks.
        try:
            uncancelled = await self._task_supervisor.shutdown()
            if uncancelled:
                msg = (
                    f"Tasks did not shut down cooperatively: {', '.join(uncancelled)}"
                )
                logger.warning(msg)
                self._close_errors.append(msg)
        except Exception as exc:
            msg = f"TaskSupervisor shutdown error: {exc}"
            logger.error(msg)
            self._close_errors.append(msg)

        # Step 3: close generic resources.
        for resource in self._resources:
            try:
                if hasattr(resource, "close") and callable(resource.close):
                    result = resource.close()
                    if inspect.iscoroutine(result):
                        await result
                elif hasattr(resource, "cancel") and callable(resource.cancel):
                    resource.cancel()
            except Exception as exc:
                msg = f"Resource close error ({type(resource).__name__}): {exc}"
                logger.error(msg)
                self._close_errors.append(msg)

        self._resources.clear()
        return self._close_errors

    def __repr__(self) -> str:
        return (
            f"ResourceScope(plugin_id={self.plugin_id!r}, "
            f"generation={self.generation}, closed={self._closed})"
        )
