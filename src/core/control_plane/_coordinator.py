"""Deployment coordinator — bridges ControlPlane deployments to PluginRuntime activation.

Handles INSTALL, UPGRADE, DISABLE, ENABLE, ROLLBACK operations with per-plugin
asyncio.Lock and deployment_id idempotency.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.bot import Bot


class CoordinatorOp(StrEnum):
    INSTALL = "install"
    UPGRADE = "upgrade"
    DISABLE = "disable"
    ENABLE = "enable"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class OperationRequest:
    op: CoordinatorOp
    plugin_id: str
    deployment_id: str
    proposal_id: str
    version: str
    enabled: bool


@dataclass
class ActivationReport:
    deployment_id: str
    plugin_id: str
    version: str
    success: bool
    runtime_generation: int = 0
    active_count: int = 0
    activated_at: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    health_status: str = "unknown"


class DeploymentCoordinator:
    """Coordinates ControlPlane deployments with PluginRuntime lifecycle.

    One asyncio.Lock per plugin_id ensures serialized access.
    deployment_id serves as an idempotency key — re-submitting the same
    ID returns the cached ActivationReport.
    """

    def __init__(self, bot: Bot, connection: sqlite3.Connection) -> None:
        self._bot = bot
        self._connection = connection
        self._locks: dict[str, asyncio.Lock] = {}
        self._reports: dict[str, ActivationReport] = {}
        self._boot_recovery_done = False

    async def _reload_runtime(
        self,
        plugin_id: str,
        *,
        expected_enabled: bool,
        expected_version: str = "",
    ) -> int:
        """Reload one plugin and verify the published Runtime snapshot."""
        cfg = self._bot.config.plugin
        runtime = self._bot.plugin_runtime
        active = await runtime.reload_plugin(
            plugin_id,
            plugin_dirs=cfg.plugin_dirs,
            disabled=cfg.disabled_plugins,
        )
        plugin = runtime.get_plugin(plugin_id)
        published = plugin_id in runtime.snapshot.plugin_ids

        if expected_enabled:
            if not active or plugin is None or not published:
                raise RuntimeError(
                    f"plugin '{plugin_id}' did not become active"
                )
            if expected_version and plugin.manifest.version != expected_version:
                raise RuntimeError(
                    f"plugin '{plugin_id}' activated version "
                    f"{plugin.manifest.version}, expected {expected_version}"
                )
        elif active or published:
            raise RuntimeError(f"plugin '{plugin_id}' is still active")

        return runtime.snapshot.plugin_count

    def _get_lock(self, plugin_id: str) -> asyncio.Lock:
        if plugin_id not in self._locks:
            self._locks[plugin_id] = asyncio.Lock()
        return self._locks[plugin_id]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def execute(self, request: OperationRequest) -> ActivationReport:
        """Execute an operation requested by the ControlPlane.

        Idempotency: if *request.deployment_id* has already been processed,
        the cached report is returned.
        """
        # Idempotency check
        if request.deployment_id and request.deployment_id in self._reports:
            return self._reports[request.deployment_id]

        lock = self._get_lock(request.plugin_id)
        async with lock:
            # Double-check inside lock
            if request.deployment_id and request.deployment_id in self._reports:
                return self._reports[request.deployment_id]

            try:
                if request.op == CoordinatorOp.INSTALL:
                    report = await self._install(request)
                elif request.op == CoordinatorOp.ENABLE:
                    report = await self._enable(request)
                elif request.op == CoordinatorOp.DISABLE:
                    report = await self._disable(request)
                elif request.op == CoordinatorOp.ROLLBACK:
                    report = await self._rollback(request)
                elif request.op == CoordinatorOp.UPGRADE:
                    report = await self._install(request)
                else:
                    report = ActivationReport(
                        deployment_id=request.deployment_id,
                        plugin_id=request.plugin_id,
                        version=request.version,
                        success=False,
                        error=f"Unknown operation: {request.op}",
                    )
            except Exception as exc:
                report = ActivationReport(
                    deployment_id=request.deployment_id,
                    plugin_id=request.plugin_id,
                    version=request.version,
                    success=False,
                    error=str(exc),
                )
                self._write_db_error(request, str(exc))

            if request.deployment_id:
                self._reports[request.deployment_id] = report
            return report

    async def recover_pending(self) -> list[ActivationReport]:
        """On boot, find deployments stuck in FILES_SWITCHED or ACTIVATING
        and complete or fail them."""
        if self._boot_recovery_done:
            return []
        self._boot_recovery_done = True

        reports: list[ActivationReport] = []

        rows = self._connection.execute(
            "SELECT deployment_id, name, version, deployment_phase "
            "FROM plugin_deployments "
            "WHERE deployment_phase IN ('files_switched', 'activating') "
            "AND (status='pending_activation' OR status='pending') "
            "ORDER BY installed_at DESC"
        ).fetchall()

        for row in rows:
            deployment_id, name, version, phase = row
            report = await self.execute(
                OperationRequest(
                    op=CoordinatorOp.INSTALL,
                    plugin_id=name,
                    deployment_id=deployment_id,
                    proposal_id="boot-recovery",
                    version=version,
                    enabled=True,
                )
            )
            if report.success:
                report.health_status = "recovered"
            reports.append(report)

        return reports

    async def reconcile(
        self,
        plugin_id: str,
        *,
        expected_enabled: bool,
        expected_version: str = "",
        deployment_id: str = "",
    ) -> ActivationReport:
        """Reconcile Runtime with compensated filesystem/config state."""
        lock = self._get_lock(plugin_id)
        async with lock:
            try:
                active_count = await self._reload_runtime(
                    plugin_id,
                    expected_enabled=expected_enabled,
                    expected_version=expected_version,
                )
                report = ActivationReport(
                    deployment_id=deployment_id,
                    plugin_id=plugin_id,
                    version=expected_version,
                    success=True,
                    runtime_generation=self._bot.plugin_runtime.generation,
                    active_count=active_count,
                    activated_at=int(time.time()),
                    health_status="active" if expected_enabled else "disabled",
                )
                if deployment_id and expected_enabled:
                    self._mark_active(deployment_id, report)
                return report
            except Exception as exc:
                if deployment_id:
                    self._write_db_error_by_id(deployment_id, str(exc))
                return ActivationReport(
                    deployment_id=deployment_id,
                    plugin_id=plugin_id,
                    version=expected_version,
                    success=False,
                    error=str(exc),
                    health_status="failed",
                )

    # ------------------------------------------------------------------
    # operation handlers
    # ------------------------------------------------------------------

    async def _install(self, request: OperationRequest) -> ActivationReport:
        """Load/reload plugins after files have been deployed.

        At this point files are already in place (FILES_SWITCHED phase).
        """
        active_count = await self._reload_runtime(
            request.plugin_id,
            expected_enabled=True,
            expected_version=request.version,
        )

        report = ActivationReport(
            deployment_id=request.deployment_id,
            plugin_id=request.plugin_id,
            version=request.version,
            success=True,
            runtime_generation=(
                self._bot.plugin_runtime.generation
                if hasattr(self._bot, "plugin_runtime")
                else 0
            ),
            active_count=active_count,
            activated_at=int(time.time()),
            health_status="active",
        )

        self._mark_active(request.deployment_id, report)
        return report

    async def _disable(self, request: OperationRequest) -> ActivationReport:
        """Add plugin to disabled set and reload."""
        # Config-based disable (already applied by ControlPlane)
        # Reload to stop the plugin
        active_count = await self._reload_runtime(
            request.plugin_id,
            expected_enabled=False,
        )

        return ActivationReport(
            deployment_id=request.deployment_id,
            plugin_id=request.plugin_id,
            version="",
            success=True,
            runtime_generation=(
                self._bot.plugin_runtime.generation
                if hasattr(self._bot, "plugin_runtime")
                else 0
            ),
            active_count=active_count,
            activated_at=int(time.time()),
            health_status="disabled",
        )

    async def _enable(self, request: OperationRequest) -> ActivationReport:
        """Remove plugin from disabled set and reload."""
        active_count = await self._reload_runtime(
            request.plugin_id,
            expected_enabled=True,
        )

        return ActivationReport(
            deployment_id=request.deployment_id,
            plugin_id=request.plugin_id,
            version="",
            success=True,
            runtime_generation=(
                self._bot.plugin_runtime.generation
                if hasattr(self._bot, "plugin_runtime")
                else 0
            ),
            active_count=active_count,
            activated_at=int(time.time()),
            health_status="enabled",
        )

    async def _rollback(self, request: OperationRequest) -> ActivationReport:
        """Files are already restored by ControlPlane — reload to activate."""
        active_count = await self._reload_runtime(
            request.plugin_id,
            expected_enabled=True,
            expected_version=request.version,
        )

        report = ActivationReport(
            deployment_id=request.deployment_id,
            plugin_id=request.plugin_id,
            version=request.version,
            success=True,
            runtime_generation=(
                self._bot.plugin_runtime.generation
                if hasattr(self._bot, "plugin_runtime")
                else 0
            ),
            active_count=active_count,
            activated_at=int(time.time()),
            health_status="active",
        )

        self._mark_active(request.deployment_id, report)
        return report

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _mark_active(self, deployment_id: str, report: ActivationReport) -> None:
        self._connection.execute(
            "UPDATE plugin_deployments SET status='active', "
            "deployment_phase='active', runtime_generation=?, "
            "activated_at=?, error_message=NULL "
            "WHERE deployment_id=?",
            (report.runtime_generation, report.activated_at, deployment_id),
        )
        self._connection.commit()

    def _write_db_error(self, request: OperationRequest, message: str) -> None:
        if request.deployment_id:
            self._write_db_error_by_id(request.deployment_id, message)

    def _write_db_error_by_id(self, deployment_id: str, message: str) -> None:
        self._connection.execute(
            "UPDATE plugin_deployments SET deployment_phase='failed', "
            "error_message=? WHERE deployment_id=?",
            (message[:2000], deployment_id),
        )
        self._connection.commit()
