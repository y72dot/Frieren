"""SchedulerAgency — permission-gated schedule management for plugins.

Wraps :class:`SchedulerService` with permission checks and automatic
``plugin_id`` / ``generation`` attribution, following the same agency
pattern as :class:`QQAgency`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.plugin.context import PermissionDeniedError

if TYPE_CHECKING:
    from src.core.runtime.scheduler import SchedulerService
    from src.plugin.manifest import ManifestPermissions


class SchedulerAgency:
    """Plugin-visible scheduler surface.

    Every schedule created through this agency is automatically tagged
    with the owning plugin's identity and generation, enabling bulk
    operations (pause-on-disable, cancel-on-stop).
    """

    def __init__(
        self,
        scheduler_service: SchedulerService,
        plugin_id: str,
        permissions: ManifestPermissions,
        generation: int,
    ) -> None:
        self._svc = scheduler_service
        self._plugin_id = plugin_id
        self._permissions = permissions
        self._generation = generation

    # ------------------------------------------------------------------
    # permission check
    # ------------------------------------------------------------------

    def _check(self) -> None:
        if not self._permissions.scheduler:
            raise PermissionDeniedError(
                self._plugin_id, "scheduler",
                "plugin manifest must set permissions.scheduler = true"
            )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_schedule(
        self,
        name: str,
        trigger_type: str,
        trigger_spec: dict[str, Any],
        task_data: dict[str, Any],
        *,
        timezone: str = "Asia/Shanghai",
        misfire_policy: str = "skip",
        max_concurrency: int = 1,
    ) -> str:
        """Create a schedule tagged with this plugin's identity.

        Returns the *schedule_id* string.
        """
        self._check()
        record = self._svc.create(
            name=name,
            trigger_type=trigger_type,
            trigger_spec=trigger_spec,
            timezone=timezone,
            task_template=task_data,
            misfire_policy=misfire_policy,
            max_concurrency=max_concurrency,
            plugin_id=self._plugin_id,
            plugin_generation=self._generation,
        )
        return record.schedule_id

    async def cancel_schedule(self, schedule_id: str) -> bool:
        """Cancel a schedule. Returns ``True`` if it belonged to this plugin."""
        self._check()
        records = self._svc.store.list_by_plugin(self._plugin_id)
        for r in records:
            if r.schedule_id == schedule_id:
                self._svc.store.delete(schedule_id)
                return True
        return False

    async def list_schedules(self) -> list[dict[str, Any]]:
        """Return all schedules owned by this plugin as dicts."""
        self._check()
        records = self._svc.store.list_by_plugin(self._plugin_id)
        return [
            {
                "schedule_id": r.schedule_id,
                "name": r.name,
                "enabled": r.enabled,
                "trigger_type": r.trigger_type,
                "trigger_spec": r.trigger_spec(),
                "next_run_at": r.next_run_at,
                "last_run_at": r.last_run_at,
            }
            for r in records
        ]

    async def pause_schedule(self, schedule_id: str) -> bool:
        """Pause (disable) a schedule owned by this plugin."""
        self._check()
        records = self._svc.store.list_by_plugin(self._plugin_id)
        for r in records:
            if r.schedule_id == schedule_id:
                self._svc.store.set_enabled(schedule_id, False)
                return True
        return False

    async def resume_schedule(self, schedule_id: str) -> bool:
        """Resume (enable) a schedule owned by this plugin."""
        self._check()
        records = self._svc.store.list_by_plugin(self._plugin_id)
        for r in records:
            if r.schedule_id == schedule_id:
                self._svc.store.set_enabled(schedule_id, True)
                return True
        return False
