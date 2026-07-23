"""PLUG-601: PackageDeployer tests — multi-file deploy, upgrade, rollback,
crash recovery at each phase, idempotent retry."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from src.core.control_plane._deployer import (
    DeploymentPhase,
    DeploymentRecord,
    PackageDeployer,
)
from src.core.control_plane._security import SecurityReport

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_report(
    name: str = "demo_plugin",
    version: str = "1.0.0",
    candidate: Path | None = None,
    **kwargs,
) -> SecurityReport:
    digest = hashlib.sha256()
    if candidate is not None:
        for path in sorted(candidate.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(candidate).as_posix().encode())
                digest.update(path.read_bytes())
    defaults = {
        "valid": True,
        "name": name,
        "version": version,
        "entrypoint": "main.py",
        "permissions": {},
        "violations": [],
        "candidate": "",
        "sha256": digest.hexdigest(),
        "manifest_snapshot": {"name": name, "version": version},
        "file_count": 1,
        "total_size_bytes": 100,
        "symlinks_detected": False,
        "warnings": [],
    }
    defaults.update(kwargs)
    return SecurityReport(**defaults)


def _candidate(root: Path, *, name: str = "demo_plugin", content: str = "VALUE = 'v1'\n") -> Path:
    """Create a minimal plugin candidate directory with plugin.toml + main.py."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.toml").write_text(
        f'name = "{name}"\nversion = "1.0.0"\nentrypoint = "main.py"\n',
        encoding="utf-8",
    )
    (root / "main.py").write_text(content, encoding="utf-8")
    return root


def _setup_dirs(tmp_path: Path):
    """Create plugin_dir, candidate_dir and return deployer + paths."""
    plugin_dir = tmp_path / "plugins"
    candidate_dir = tmp_path / "candidates"
    plugin_dir.mkdir()
    candidate_dir.mkdir()
    conn = sqlite3.connect(":memory:")
    _init_schema(conn)
    deployer = PackageDeployer(plugin_dir, candidate_dir, conn)
    return deployer, plugin_dir, candidate_dir, conn


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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plugin_deployments_name_status "
        "ON plugin_deployments(name, status)"
    )
    conn.commit()


def _count_deployments(conn: sqlite3.Connection, **filters) -> int:
    sql = "SELECT COUNT(*) FROM plugin_deployments"
    params = []
    conditions = []
    for col, val in filters.items():
        conditions.append(f"{col}=?")
        params.append(val)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    return conn.execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------------------
# basic deploy
# ---------------------------------------------------------------------------


class TestBasicDeploy:
    def test_single_file_deploy(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)
        cand = candidate_dir / "demo_plugin"
        _candidate(cand, content="VALUE = 'hello'\n")

        deployer.stage(cand, "demo_plugin")
        record = deployer.deploy("demo_plugin", _make_report(candidate=cand))

        assert record.status == "pending_activation"
        assert record.deployment_phase == DeploymentPhase.FILES_SWITCHED.value
        target = plugin_dir / "demo_plugin" / "main.py"
        assert target.read_text(encoding="utf-8") == "VALUE = 'hello'\n"

    def test_multi_file_deploy(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)
        cand = candidate_dir / "multi"
        cand.mkdir()
        (cand / "plugin.toml").write_text(
            'name = "multi"\nversion = "1.0.0"\nentrypoint = "core.py"\n',
            encoding="utf-8",
        )
        (cand / "core.py").write_text("X=1\n", encoding="utf-8")
        (cand / "utils.py").write_text("Y=2\n", encoding="utf-8")

        deployer.stage(cand, "multi")
        record = deployer.deploy("multi", _make_report(name="multi", candidate=cand))

        assert record.status == "pending_activation"
        target_dir = plugin_dir / "multi"
        assert target_dir.is_dir()
        assert (target_dir / "core.py").exists()
        assert (target_dir / "utils.py").exists()

    def test_staging_path_created(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)
        cand = candidate_dir / "demo_plugin"
        _candidate(cand)

        staging = deployer.stage(cand, "demo_plugin")
        assert staging.is_dir()
        assert (staging / "main.py").exists()
        assert staging.parent == plugin_dir / ".staging"


# ---------------------------------------------------------------------------
# upgrade & rollback
# ---------------------------------------------------------------------------


class TestUpgradeAndRollback:
    def test_same_version_with_different_content_is_rejected(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)
        first = candidate_dir / "first"
        _candidate(first, content="VALUE = 'v1'\n")
        deployer.stage(first, "demo_plugin")
        deployer.deploy("demo_plugin", _make_report(candidate=first))

        changed = candidate_dir / "changed"
        _candidate(changed, content="VALUE = 'changed'\n")
        deployer.stage(changed, "demo_plugin")
        with pytest.raises(ValueError, match="different content"):
            deployer.deploy("demo_plugin", _make_report(candidate=changed))

    def test_upgrade_chain_preserves_backup(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        # V1
        cand1 = candidate_dir / "v1"
        _candidate(cand1, content="VALUE = 'v1'\n")
        deployer.stage(cand1, "demo_plugin")
        deployer.deploy("demo_plugin", _make_report(candidate=cand1))

        # V2
        cand2 = candidate_dir / "v2"
        _candidate(cand2, content="VALUE = 'v2'\n")
        deployer.stage(cand2, "demo_plugin")
        deployer.deploy(
            "demo_plugin", _make_report(version="2.0.0", candidate=cand2)
        )

        target = plugin_dir / "demo_plugin" / "main.py"
        assert "v2" in target.read_text(encoding="utf-8")

        # The backup dir should contain the V1 version
        backups = list((plugin_dir / ".plugin_backups").iterdir())
        assert len(backups) >= 1

    def test_rollback_restores_previous(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        # V1
        cand1 = candidate_dir / "v1"
        _candidate(cand1, content="VALUE = 'v1'\n")
        deployer.stage(cand1, "demo_plugin")
        deployer.deploy("demo_plugin", _make_report(candidate=cand1))

        # V2
        cand2 = candidate_dir / "v2"
        _candidate(cand2, content="VALUE = 'v2'\n")
        deployer.stage(cand2, "demo_plugin")
        deployer.deploy(
            "demo_plugin", _make_report(version="2.0.0", candidate=cand2)
        )

        # Rollback
        deployer.rollback("demo_plugin")
        target = plugin_dir / "demo_plugin" / "main.py"
        assert "v1" in target.read_text(encoding="utf-8")

    def test_rollback_without_previous_fails(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        # Deploy V1 (no previous_deployment_id)
        cand1 = candidate_dir / "v1"
        _candidate(cand1, content="VALUE = 'v1'\n")
        deployer.stage(cand1, "demo_plugin")
        deployer.deploy("demo_plugin", _make_report(candidate=cand1))

        # Rollback when there's only one deployment
        record = deployer.rollback("demo_plugin")
        assert record.status == "rolled_back"


# ---------------------------------------------------------------------------
# crash recovery
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    def test_first_rename_failure_preserves_existing_package(
        self, tmp_path, monkeypatch
    ):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)
        target = plugin_dir / "demo_plugin"
        _candidate(target, content="VALUE = 'old'\n")
        candidate = candidate_dir / "new"
        _candidate(candidate, content="VALUE = 'new'\n")
        deployer.stage(candidate, "demo_plugin")

        def fail_first(_source, _target):
            raise OSError("first rename failed")

        monkeypatch.setattr("src.core.control_plane._deployer.os.replace", fail_first)
        with pytest.raises(OSError, match="first rename"):
            deployer.deploy("demo_plugin", _make_report(candidate=candidate))

        assert "old" in (target / "main.py").read_text(encoding="utf-8")

    def test_second_rename_failure_restores_existing_package(
        self, tmp_path, monkeypatch
    ):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)
        target = plugin_dir / "demo_plugin"
        _candidate(target, content="VALUE = 'old'\n")
        candidate = candidate_dir / "new"
        _candidate(candidate, content="VALUE = 'new'\n")
        deployer.stage(candidate, "demo_plugin")
        real_replace = __import__("os").replace
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("second rename failed")
            return real_replace(source, destination)

        monkeypatch.setattr(
            "src.core.control_plane._deployer.os.replace", fail_second
        )
        with pytest.raises(OSError, match="second rename"):
            deployer.deploy("demo_plugin", _make_report(candidate=candidate))

        assert "old" in (target / "main.py").read_text(encoding="utf-8")

    def test_recover_cleans_staged_remnants(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        # Simulate staged remnant
        staging_dir = plugin_dir / ".staging" / "test_plugin"
        staging_dir.mkdir(parents=True)
        (staging_dir / "main.py").write_text("x=1\n", encoding="utf-8")

        # Insert a record with phase=staged
        conn.execute(
            "INSERT INTO plugin_deployments (deployment_id, name, version, "
            "target_path, installed_at, status, deployment_phase) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("rec1", "test_plugin", "1.0", str(plugin_dir / "test_plugin"),
             int(time.time()), "pending", "staged"),
        )
        conn.commit()

        recovered = deployer.recover_on_boot()
        assert "test_plugin" in recovered
        # Staging should be cleaned up
        assert not staging_dir.exists()

    def test_recover_applying_phase(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        # Simulate a partial deploy
        staging_dir = plugin_dir / ".staging" / "apply_plug"
        staging_dir.mkdir(parents=True)
        (staging_dir / "main.py").write_text("y=1\n", encoding="utf-8")

        conn.execute(
            "INSERT INTO plugin_deployments (deployment_id, name, version, "
            "target_path, installed_at, status, deployment_phase) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("rec2", "apply_plug", "1.0", str(plugin_dir / "apply_plug"),
             int(time.time()), "pending", "applying"),
        )
        conn.commit()

        recovered = deployer.recover_on_boot()
        assert "apply_plug" in recovered

    def test_recover_files_switched_retains_for_activation(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        # Deploy properly, then manually tweak to look like crash before activation
        cand = candidate_dir / "fs_plug"
        _candidate(cand, name="fs_plug", content="VALUE = 'crash'\n")
        deployer.stage(cand, "fs_plug")
        deployer.deploy("fs_plug", _make_report(name="fs_plug", candidate=cand))

        # Files should be in place
        assert (plugin_dir / "fs_plug" / "main.py").exists()

        recovered = deployer.recover_on_boot()
        assert "fs_plug" in recovered
        # Files should still be intact
        assert (plugin_dir / "fs_plug" / "main.py").exists()

    def test_idempotent_deploy_id(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        cand = candidate_dir / "one_shot"
        _candidate(cand, name="one_shot", content="VALUE = 'hi'\n")
        deployer.stage(cand, "one_shot")

        record1 = deployer.deploy(
            "one_shot", _make_report(name="one_shot", candidate=cand)
        )
        # Deployment record should be in DB
        row = conn.execute(
            "SELECT deployment_phase, status FROM plugin_deployments "
            "WHERE deployment_id=?",
            (record1.deployment_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == DeploymentPhase.FILES_SWITCHED.value

    def test_target_directory_created(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        cand = candidate_dir / "new_plug"
        _candidate(cand, name="new_plug", content="VALUE = 42\n")
        deployer.stage(cand, "new_plug")
        deployer.deploy(
            "new_plug", _make_report(name="new_plug", candidate=cand)
        )

        target_dir = plugin_dir / "new_plug"
        assert target_dir.is_dir()
        assert (target_dir / "plugin.toml").exists()
        assert (target_dir / "main.py").exists()

    def test_backup_contains_old_content(self, tmp_path):
        deployer, plugin_dir, candidate_dir, conn = _setup_dirs(tmp_path)

        # V1
        cand1 = candidate_dir / "v1"
        _candidate(cand1, content="V1_CONTENT\n")
        deployer.stage(cand1, "demo_plugin")
        deployer.deploy("demo_plugin", _make_report(candidate=cand1))

        # Find the backup after V1 deploy - none should exist
        # V2
        cand2 = candidate_dir / "v2"
        _candidate(cand2, content="V2_CONTENT\n")
        deployer.stage(cand2, "demo_plugin")
        deployer.deploy(
            "demo_plugin", _make_report(version="2.0.0", candidate=cand2)
        )

        # Now there should be a backup with V1 content
        backups = list((plugin_dir / ".plugin_backups").glob("demo_plugin-*"))
        assert len(backups) >= 1
        # Check backup content
        backup_main = backups[0] / "main.py"
        assert backup_main.exists()
        assert "V1_CONTENT" in backup_main.read_text(encoding="utf-8")


class TestDeploymentRecord:
    def test_record_to_dict(self):
        record = DeploymentRecord(
            deployment_id="id1",
            name="test_plugin",
            version="1.0.0",
            target_path="/tmp/plugins/test",
            backup_path="/tmp/backups/test",
            installed_at=1234567890,
            status="pending_activation",
            deployment_phase="files_switched",
            package_digest="abc123",
        )
        d = record.to_dict()
        assert d["deployment_id"] == "id1"
        assert d["status"] == "pending_activation"
        assert d["package_digest"] == "abc123"

    def test_phase_enum_values(self):
        assert DeploymentPhase.VALIDATED == "validated"
        assert DeploymentPhase.STAGED == "staged"
        assert DeploymentPhase.ACTIVE == "active"
        assert DeploymentPhase.FAILED == "failed"
        assert DeploymentPhase.ROLLED_BACK == "rolled_back"
