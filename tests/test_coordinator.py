"""PLUG-602: DeploymentCoordinator tests — install→runtime, upgrade chain,
disable/enable, rollback, concurrent serialization, idempotent replay,
recover_pending, activation failure handling."""

from __future__ import annotations

import sqlite3
import time

import pytest

from src.core.control_plane._coordinator import (
    ActivationReport,
    CoordinatorOp,
    DeploymentCoordinator,
    OperationRequest,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakePluginRuntime:
    """Minimal fake that mimics targeted PluginRuntime reload."""

    def __init__(self):
        self.generation = 0
        self.reload_count = 0
        self.versions = {"test_plugin": "1.0.0", "plug_a": "1.0.0", "plug_b": "1.0.0"}
        self._active: set[str] = set()
        self.fail_plugins: set[str] = set()

    async def reload_plugin(self, plugin_id, *, plugin_dirs, disabled):
        self.reload_count += 1
        self.generation += 1
        if plugin_id in disabled:
            self._active.discard(plugin_id)
            return False
        if plugin_id in self.fail_plugins:
            self._active.discard(plugin_id)
            return False
        self._active.add(plugin_id)
        return True

    def get_plugin(self, plugin_id):
        if plugin_id not in self._active:
            return None
        return type(
            "Loaded",
            (),
            {"manifest": type("Manifest", (), {"version": self.versions[plugin_id]})()},
        )()

    @property
    def snapshot(self):
        return type(
            "Snapshot",
            (),
            {
                "plugin_ids": frozenset(self._active),
                "plugin_count": len(self._active),
            },
        )()


class _FakeConfigPlugin:
    def __init__(self):
        self.plugin_dirs: list[str] = ["plugins"]
        self.disabled_plugins: list[str] = []


class _FakeConfig:
    def __init__(self):
        self.plugin = _FakeConfigPlugin()


class _FakeBot:
    """Minimal fake Bot for coordinator tests."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.plugin_runtime = _FakePluginRuntime()
        self.config = _FakeConfig()


def _init_schema(conn: sqlite3.Connection):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS plugin_deployments (
            deployment_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            target_path TEXT NOT NULL,
            backup_path TEXT,
            installed_at INTEGER NOT NULL,
            status TEXT NOT NULL,
            deployment_phase TEXT NOT NULL DEFAULT 'active',
            package_digest TEXT NOT NULL DEFAULT '',
            manifest_snapshot TEXT,
            permissions_snapshot TEXT,
            previous_deployment_id TEXT,
            runtime_generation INTEGER NOT NULL DEFAULT 0,
            activated_at INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            validation_summary TEXT
        )"""
    )
    conn.commit()


def _make_request(
    op: CoordinatorOp = CoordinatorOp.INSTALL,
    plugin_id: str = "test_plugin",
    deployment_id: str = "deploy-1",
    version: str = "1.0.0",
    enabled: bool = True,
) -> OperationRequest:
    return OperationRequest(
        op=op,
        plugin_id=plugin_id,
        deployment_id=deployment_id,
        proposal_id="prop-1",
        version=version,
        enabled=enabled,
    )


def _insert_deployment(
    conn: sqlite3.Connection,
    deployment_id: str,
    name: str,
    version: str = "1.0.0",
    phase: str = "files_switched",
    status: str = "pending_activation",
):
    conn.execute(
        """INSERT INTO plugin_deployments (
            deployment_id, name, version, target_path,
            installed_at, status, deployment_phase
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            deployment_id,
            name,
            version,
            f"/tmp/plugins/{name}",
            int(time.time()),
            status,
            phase,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# basic operations
# ---------------------------------------------------------------------------


class TestCoordinatorBasic:
    @pytest.mark.asyncio
    async def test_install_activates_in_runtime(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        coord = DeploymentCoordinator(bot, conn)

        request = _make_request()
        _insert_deployment(conn, request.deployment_id, request.plugin_id)

        report = await coord.execute(request)
        assert report.success is True
        assert report.plugin_id == "test_plugin"
        assert report.health_status == "active"

        # Runtime should have been reloaded
        assert bot.plugin_runtime.reload_count == 1

        # DB should be marked active
        row = conn.execute(
            "SELECT status, deployment_phase, runtime_generation "
            "FROM plugin_deployments WHERE deployment_id=?",
            (request.deployment_id,),
        ).fetchone()
        assert row[0] == "active"
        assert row[1] == "active"
        assert row[2] == bot.plugin_runtime.generation

    @pytest.mark.asyncio
    async def test_disable_reloads(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        bot.config.plugin.disabled_plugins = ["test_plugin"]
        coord = DeploymentCoordinator(bot, conn)

        request = _make_request(op=CoordinatorOp.DISABLE)
        report = await coord.execute(request)
        assert report.success is True
        assert report.health_status == "disabled"
        assert bot.plugin_runtime.reload_count == 1

    @pytest.mark.asyncio
    async def test_enable_reloads(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        coord = DeploymentCoordinator(bot, conn)

        request = _make_request(op=CoordinatorOp.ENABLE)
        report = await coord.execute(request)
        assert report.success is True
        assert report.health_status == "enabled"
        assert bot.plugin_runtime.reload_count == 1

    @pytest.mark.asyncio
    async def test_rollback_activates(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        bot.plugin_runtime.versions["test_plugin"] = "0.9.0"
        coord = DeploymentCoordinator(bot, conn)

        request = _make_request(op=CoordinatorOp.ROLLBACK, version="0.9.0")
        _insert_deployment(conn, request.deployment_id, request.plugin_id, version="0.9.0")

        report = await coord.execute(request)
        assert report.success is True
        assert report.version == "0.9.0"
        assert report.health_status == "active"

    @pytest.mark.asyncio
    async def test_upgrade_is_same_as_install(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        bot.plugin_runtime.versions["test_plugin"] = "2.0.0"
        coord = DeploymentCoordinator(bot, conn)

        request = _make_request(op=CoordinatorOp.UPGRADE, version="2.0.0")
        _insert_deployment(conn, request.deployment_id, request.plugin_id, version="2.0.0")

        report = await coord.execute(request)
        assert report.success is True
        assert bot.plugin_runtime.reload_count == 1

    @pytest.mark.asyncio
    async def test_install_is_not_marked_active_when_target_fails(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        bot.plugin_runtime.fail_plugins.add("test_plugin")
        coord = DeploymentCoordinator(bot, conn)
        request = _make_request()
        _insert_deployment(conn, request.deployment_id, request.plugin_id)

        report = await coord.execute(request)

        assert report.success is False
        row = conn.execute(
            "SELECT status, deployment_phase FROM plugin_deployments "
            "WHERE deployment_id=?",
            (request.deployment_id,),
        ).fetchone()
        assert row == ("pending_activation", "failed")


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


class TestCoordinatorIdempotency:
    @pytest.mark.asyncio
    async def test_same_deployment_id_returns_cached(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        coord = DeploymentCoordinator(bot, conn)

        request = _make_request()
        _insert_deployment(conn, request.deployment_id, request.plugin_id)

        report1 = await coord.execute(request)
        report2 = await coord.execute(request)

        # Same deployment_id should return cached result without re-executing
        assert report1 is report2
        # Runtime should only be reloaded once
        assert bot.plugin_runtime.reload_count == 1

    @pytest.mark.asyncio
    async def test_different_deployment_id_executes_again(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        coord = DeploymentCoordinator(bot, conn)

        req1 = _make_request(deployment_id="deploy-1")
        req2 = _make_request(deployment_id="deploy-2")
        _insert_deployment(conn, "deploy-1", "test_plugin")
        _insert_deployment(conn, "deploy-2", "test_plugin", version="2.0.0")

        report1 = await coord.execute(req1)
        report2 = await coord.execute(req2)

        assert report1 is not report2
        assert bot.plugin_runtime.reload_count == 2


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


class TestCoordinatorConcurrency:
    @pytest.mark.asyncio
    async def test_serialized_access_per_plugin(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        coord = DeploymentCoordinator(bot, conn)

        req1 = _make_request(deployment_id="deploy-1")
        req2 = _make_request(deployment_id="deploy-2")
        _insert_deployment(conn, "deploy-1", "test_plugin")
        _insert_deployment(conn, "deploy-2", "test_plugin", version="2.0.0")

        import asyncio

        results = await asyncio.gather(coord.execute(req1), coord.execute(req2))
        assert all(r.success for r in results)
        # Both should have executed (serialized)
        assert bot.plugin_runtime.reload_count == 2


# ---------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------


class TestCoordinatorRecovery:
    @pytest.mark.asyncio
    async def test_recover_pending_activates(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        coord = DeploymentCoordinator(bot, conn)

        _insert_deployment(conn, "rec1", "plug_a", phase="files_switched")
        _insert_deployment(conn, "rec2", "plug_b", phase="activating")

        reports = await coord.recover_pending()
        assert len(reports) == 2
        assert all(r.success for r in reports)
        assert reports[0].health_status == "recovered"

        # Runtime should be reloaded twice (once per plugin)
        assert bot.plugin_runtime.reload_count == 2

    @pytest.mark.asyncio
    async def test_recover_pending_only_runs_once(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        coord = DeploymentCoordinator(bot, conn)

        _insert_deployment(conn, "rec1", "plug_a")

        reports1 = await coord.recover_pending()
        reports2 = await coord.recover_pending()

        assert len(reports1) == 1
        assert len(reports2) == 0  # Second call is no-op

    @pytest.mark.asyncio
    async def test_unknown_op_returns_error(self):
        conn = sqlite3.connect(":memory:")
        _init_schema(conn)
        bot = _FakeBot(conn)
        coord = DeploymentCoordinator(bot, conn)

        request = _make_request()
        # Use a fake op value by bypassing the enum
        request = OperationRequest(
            op="invalid_op",  # type: ignore[arg-type]
            plugin_id="test",
            deployment_id="deploy-1",
            proposal_id="prop-1",
            version="1.0",
            enabled=True,
        )

        report = await coord.execute(request)
        assert report.success is False
        assert "Unknown operation" in (report.error or "")


# ---------------------------------------------------------------------------
# ActivationReport
# ---------------------------------------------------------------------------


class TestActivationReport:
    def test_report_fields(self):
        report = ActivationReport(
            deployment_id="d1",
            plugin_id="p1",
            version="1.0.0",
            success=True,
            runtime_generation=3,
            active_count=5,
            activated_at=1234567890,
            error=None,
            warnings=["test"],
            health_status="active",
        )
        assert report.plugin_id == "p1"
        assert report.success is True
        assert report.runtime_generation == 3
        assert report.warnings == ["test"]

    def test_failed_report(self):
        report = ActivationReport(
            deployment_id="d2",
            plugin_id="p2",
            version="2.0.0",
            success=False,
            error="Something went wrong",
            health_status="failed",
        )
        assert report.success is False
        assert "wrong" in (report.error or "")
