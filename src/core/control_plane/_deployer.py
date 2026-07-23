"""Crash-safe multi-file plugin package deployment.

Orchestrates a state machine with two-phase renames on the same filesystem
partition to guarantee atomicity. On boot, incomplete deployments are
detected and recovered.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DeploymentPhase(StrEnum):
    VALIDATED = "validated"
    STAGED = "staged"
    APPLYING = "applying"
    FILES_SWITCHED = "files_switched"
    ACTIVATING = "activating"
    ACTIVE = "active"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class DeploymentRecord:
    deployment_id: str
    name: str
    version: str
    target_path: str
    backup_path: str | None
    installed_at: int
    status: str
    deployment_phase: str = DeploymentPhase.ACTIVE.value
    package_digest: str = ""
    manifest_snapshot: str | None = None
    permissions_snapshot: str | None = None
    previous_deployment_id: str | None = None
    runtime_generation: int = 0
    activated_at: int = 0
    error_message: str | None = None
    validation_summary: str | None = None

    def to_dict(self) -> dict:
        return {
            "deployment_id": self.deployment_id,
            "name": self.name,
            "version": self.version,
            "target_path": self.target_path,
            "backup_path": self.backup_path,
            "installed_at": self.installed_at,
            "status": self.status,
            "deployment_phase": self.deployment_phase,
            "package_digest": self.package_digest,
            "manifest_snapshot": self.manifest_snapshot,
            "permissions_snapshot": self.permissions_snapshot,
            "previous_deployment_id": self.previous_deployment_id,
            "runtime_generation": self.runtime_generation,
            "activated_at": self.activated_at,
            "error_message": self.error_message,
            "validation_summary": self.validation_summary,
        }


class PackageDeployer:
    """Crash-safe deployment of multi-file plugin packages.

    Uses two-phase rename (os.replace) on the same filesystem partition:

    1. ``os.replace`` current ``plugins/<id>/`` → ``.plugin_backups/<id>-<uuid>/``
    2. ``os.replace`` ``.staging/<id>/`` → ``plugins/<id>/``

    DB phase is recorded before each switch. On recovery, the last known
    phase is checked against what's on disk to complete or roll back.
    """

    _COLUMNS = (
        "deployment_id, name, version, target_path, backup_path, "
        "installed_at, status, deployment_phase, package_digest, "
        "manifest_snapshot, permissions_snapshot, previous_deployment_id, "
        "runtime_generation, activated_at, error_message, validation_summary"
    )

    def __init__(
        self,
        plugin_dir: Path,
        candidate_dir: Path,
        connection: sqlite3.Connection,
    ) -> None:
        self._plugin_dir = plugin_dir.resolve()
        self._candidate_dir = candidate_dir.resolve()
        self._connection = connection
        self._staging_dir = self._plugin_dir / ".staging"
        self._backup_dir = self._plugin_dir / ".plugin_backups"

    # ------------------------------------------------------------------
    # staging
    # ------------------------------------------------------------------

    def stage(self, candidate_root: Path, plugin_id: str) -> Path:
        """Copy candidate directory into ``.staging/<plugin_id>/``.

        Returns the staging path.
        """
        staging_path = self._staging_dir / plugin_id
        if staging_path.exists():
            shutil.rmtree(staging_path)
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(candidate_root, staging_path)
        return staging_path

    # ------------------------------------------------------------------
    # deploy
    # ------------------------------------------------------------------

    def deploy(self, plugin_id: str, report) -> DeploymentRecord:
        """Run the full deployment state machine for *plugin_id*.

        *report* must be a ``SecurityReport`` (or compatible object) with
        fields ``name``, ``version``, ``sha256``, ``manifest_snapshot``,
        ``permissions``, ``file_count``, ``total_size_bytes``.
        """
        staging_path = self._staging_dir / plugin_id
        if not staging_path.is_dir():
            raise FileNotFoundError(f"staging directory not found: {staging_path}")
        staged_digest = self._package_digest(staging_path)
        if staged_digest != report.sha256:
            raise ValueError("staged plugin package digest does not match validation")

        same_version = self._connection.execute(
            f"SELECT {self._COLUMNS} FROM plugin_deployments "
            "WHERE name=? AND version=? "
            "AND status IN ('active', 'pending_activation') "
            "ORDER BY installed_at DESC LIMIT 1",
            (plugin_id, report.version),
        ).fetchone()
        if same_version is not None:
            existing = self._row_to_record(same_version)
            if existing.package_digest != report.sha256:
                raise ValueError(
                    f"plugin '{plugin_id}' version {report.version} "
                    "already exists with different content"
                )
            shutil.rmtree(staging_path)
            return existing

        deployment_id = uuid.uuid4().hex
        target = self._plugin_dir / plugin_id

        previous_id = self._find_active_deployment_id(plugin_id)

        # --- Phase: STAGED → APPLYING ---
        record = self._insert_deployment(
            deployment_id=deployment_id,
            plugin_id=plugin_id,
            version=report.version,
            target=str(target),
            backup=None,
            phase=DeploymentPhase.APPLYING,
            status="pending",
            digest=report.sha256,
            manifest=json.dumps(report.manifest_snapshot, ensure_ascii=False)
            if report.manifest_snapshot
            else None,
            permissions=json.dumps(report.permissions, ensure_ascii=False)
            if report.permissions
            else None,
            previous_id=previous_id,
            validation_summary=json.dumps(
                {
                    "valid": report.valid,
                    "file_count": report.file_count,
                    "total_size_bytes": report.total_size_bytes,
                    "warnings": report.warnings,
                },
                ensure_ascii=False,
            ),
        )

        backup_path: Path | None = None
        try:
            # --- Phase: APPLYING → FILES_SWITCHED ---
            self._backup_dir.mkdir(exist_ok=True)

            # 1. Backup existing plugin directory
            if target.is_dir():
                backup_path = self._backup_dir / f"{plugin_id}-{uuid.uuid4().hex}"
                self._update_phase(
                    deployment_id,
                    DeploymentPhase.APPLYING,
                    backup=str(backup_path),
                )
                os.replace(str(target), str(backup_path))
            else:
                self._update_phase(deployment_id, DeploymentPhase.APPLYING)

            # 2. Move staging to plugins
            os.replace(str(staging_path), str(target))

            self._update_phase(deployment_id, DeploymentPhase.FILES_SWITCHED, backup=str(backup_path) if backup_path else None)

            # Persist status as pending_activation (coordinator will finalize)
            self._connection.execute(
                "UPDATE plugin_deployments SET status='pending_activation' "
                "WHERE deployment_id=?",
                (deployment_id,),
            )

            # Mark previous deployments as superseded
            self._connection.execute(
                "UPDATE plugin_deployments SET status='superseded' "
                "WHERE name=? AND (status='active' OR status='pending_activation') "
                "AND deployment_id != ?",
                (plugin_id, deployment_id),
            )

            record.backup_path = str(backup_path) if backup_path else None
            record.status = "pending_activation"
            record.deployment_phase = DeploymentPhase.FILES_SWITCHED.value
            record.previous_deployment_id = previous_id

        except Exception as exc:
            if backup_path is not None:
                if backup_path.is_dir():
                    if target.is_dir():
                        shutil.rmtree(target)
                    os.replace(str(backup_path), str(target))
            elif target.is_dir():
                shutil.rmtree(target)
            self._update_phase(
                deployment_id,
                DeploymentPhase.FAILED,
                error=str(exc),
            )
            self._connection.execute(
                "UPDATE plugin_deployments SET status='failed' "
                "WHERE deployment_id=?",
                (deployment_id,),
            )
            if previous_id:
                self._connection.execute(
                    "UPDATE plugin_deployments SET status='active' "
                    "WHERE deployment_id=?",
                    (previous_id,),
                )
            self._connection.commit()
            raise

        self._connection.commit()
        return record

    # ------------------------------------------------------------------
    # rollback
    # ------------------------------------------------------------------

    def rollback(self, plugin_id: str) -> DeploymentRecord:
        """Restore the plugin files from the most recent backup.

        Returns the *previous* deployment record that was restored.
        The caller is responsible for updating deployment status/phases.
        """
        row = self._connection.execute(
            f"SELECT {self._COLUMNS} FROM plugin_deployments "
            "WHERE name=? AND (status='active' OR status='pending_activation' "
            "OR status='pending') "
            "ORDER BY installed_at DESC LIMIT 1",
            (plugin_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"no active deployment for plugin: {plugin_id}")

        record = self._row_to_record(row)
        target = Path(record.target_path)

        # Find the previous deployment (the one we want to roll back TO)
        prev_row = None
        if record.previous_deployment_id:
            prev_row = self._connection.execute(
                f"SELECT {self._COLUMNS} FROM plugin_deployments "
                "WHERE deployment_id=?",
                (record.previous_deployment_id,),
            ).fetchone()

        if record.backup_path and Path(record.backup_path).is_dir():
            # Remove current and restore backup
            if target.is_dir():
                shutil.rmtree(target)
            os.replace(str(Path(record.backup_path)), str(target))
        elif record.backup_path and Path(record.backup_path).is_file():
            if target.is_file():
                target.unlink(missing_ok=True)
            shutil.copy2(Path(record.backup_path), target)
        else:
            # No backup — remove the plugin
            if target.is_dir():
                shutil.rmtree(target)
            elif target.is_file():
                target.unlink(missing_ok=True)

        # Mark current deployment as rolled_back
        self._connection.execute(
            "UPDATE plugin_deployments SET status='rolled_back', "
            "deployment_phase=? WHERE deployment_id=?",
            (DeploymentPhase.ROLLED_BACK.value, record.deployment_id),
        )

        if prev_row:
            # Reactivate the previous deployment
            prev_record = self._row_to_record(prev_row)
            self._connection.execute(
                "UPDATE plugin_deployments SET status='pending_activation', "
                "deployment_phase=? WHERE deployment_id=?",
                (DeploymentPhase.FILES_SWITCHED.value, prev_record.deployment_id),
            )
            self._connection.commit()
            prev_record.status = "pending_activation"
            prev_record.deployment_phase = DeploymentPhase.FILES_SWITCHED.value
            return prev_record

        self._connection.commit()
        record.status = "rolled_back"
        record.deployment_phase = DeploymentPhase.ROLLED_BACK.value
        return record

    # ------------------------------------------------------------------
    # recovery
    # ------------------------------------------------------------------

    def recover_on_boot(self) -> list[str]:
        """Find and recover incomplete deployments.

        Returns a list of plugin IDs that were recovered.
        """
        recovered: list[str] = []

        # Find deployments stuck in APPLYING or STAGED
        rows = self._connection.execute(
            f"SELECT {self._COLUMNS} FROM plugin_deployments "
            "WHERE deployment_phase IN "
            "('applying', 'files_switched', 'activating', 'staged') "
            "OR (deployment_phase='failed' "
            "AND status IN ('pending', 'pending_activation')) "
            "ORDER BY installed_at DESC"
        ).fetchall()

        for row in rows:
            record = self._row_to_record(row)
            staging = self._staging_dir / record.name

            if record.deployment_phase == DeploymentPhase.STAGED.value:
                if staging.is_dir():
                    shutil.rmtree(staging)
                self._connection.execute(
                    "UPDATE plugin_deployments SET deployment_phase=?, "
                    "status='failed', error_message='Recovery: cleaned up after crash' "
                    "WHERE deployment_id=?",
                    (DeploymentPhase.FAILED.value, record.deployment_id),
                )
                recovered.append(record.name)

            elif record.deployment_phase in (
                DeploymentPhase.APPLYING.value,
                DeploymentPhase.FAILED.value,
            ):
                if staging.is_dir():
                    shutil.rmtree(staging)
                target = Path(record.target_path)
                backup = Path(record.backup_path) if record.backup_path else None
                if backup is not None:
                    if backup.is_dir():
                        if target.is_dir():
                            shutil.rmtree(target)
                        os.replace(backup, target)
                    # If the backup path was journaled but does not exist,
                    # the crash happened before the first rename; target is
                    # still the old package and must be left untouched.
                elif target.is_dir():
                    # No prior package existed, so a target in APPLYING is
                    # the partially switched new package.
                    shutil.rmtree(target)
                self._connection.execute(
                    "UPDATE plugin_deployments SET deployment_phase=?, "
                    "status='failed', error_message='Recovery: cleaned up after crash' "
                    "WHERE deployment_id=?",
                    (DeploymentPhase.FAILED.value, record.deployment_id),
                )
                if record.previous_deployment_id:
                    self._connection.execute(
                        "UPDATE plugin_deployments SET deployment_phase=?, "
                        "status='pending_activation', error_message=NULL "
                        "WHERE deployment_id=?",
                        (
                            DeploymentPhase.FILES_SWITCHED.value,
                            record.previous_deployment_id,
                        ),
                    )
                recovered.append(record.name)

            elif record.deployment_phase == DeploymentPhase.FILES_SWITCHED.value:
                # Files are in place but activation didn't happen
                # Leave as-is for the coordinator to finish activation
                recovered.append(record.name)

            elif record.deployment_phase == DeploymentPhase.ACTIVATING.value:
                # Activation was in progress — retry
                recovered.append(record.name)

        self._connection.commit()
        return recovered

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _find_active_deployment_id(self, plugin_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT deployment_id FROM plugin_deployments "
            "WHERE name=? AND (status='active' OR status='pending_activation') "
            "ORDER BY installed_at DESC LIMIT 1",
            (plugin_id,),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _package_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def _insert_deployment(
        self,
        *,
        deployment_id: str,
        plugin_id: str,
        version: str,
        target: str,
        backup: str | None,
        phase: DeploymentPhase,
        status: str,
        digest: str,
        manifest: str | None,
        permissions: str | None,
        previous_id: str | None,
        validation_summary: str | None,
    ) -> DeploymentRecord:
        now = int(time.time())
        self._connection.execute(
            """INSERT INTO plugin_deployments (
                deployment_id, name, version, target_path, backup_path,
                installed_at, status, deployment_phase, package_digest,
                manifest_snapshot, permissions_snapshot, previous_deployment_id,
                runtime_generation, activated_at, error_message, validation_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, ?)""",
            (
                deployment_id,
                plugin_id,
                version,
                target,
                backup,
                now,
                status,
                phase.value,
                digest,
                manifest,
                permissions,
                previous_id,
                validation_summary,
            ),
        )
        return DeploymentRecord(
            deployment_id=deployment_id,
            name=plugin_id,
            version=version,
            target_path=target,
            backup_path=backup,
            installed_at=now,
            status=status,
            deployment_phase=phase.value,
            package_digest=digest,
            manifest_snapshot=manifest,
            permissions_snapshot=permissions,
            previous_deployment_id=previous_id,
            validation_summary=validation_summary,
        )

    def _update_phase(
        self,
        deployment_id: str,
        phase: DeploymentPhase,
        *,
        backup: str | None = None,
        error: str | None = None,
    ) -> None:
        if backup is not None:
            self._connection.execute(
                "UPDATE plugin_deployments SET deployment_phase=?, backup_path=? "
                "WHERE deployment_id=?",
                (phase.value, backup, deployment_id),
            )
        elif error is not None:
            self._connection.execute(
                "UPDATE plugin_deployments SET deployment_phase=?, error_message=? "
                "WHERE deployment_id=?",
                (phase.value, error, deployment_id),
            )
        else:
            self._connection.execute(
                "UPDATE plugin_deployments SET deployment_phase=? "
                "WHERE deployment_id=?",
                (phase.value, deployment_id),
            )
        self._connection.commit()

    def _row_to_record(self, row: tuple) -> DeploymentRecord:
        return DeploymentRecord(
            deployment_id=row[0],
            name=row[1],
            version=row[2],
            target_path=row[3],
            backup_path=row[4],
            installed_at=row[5],
            status=row[6],
            deployment_phase=row[7],
            package_digest=row[8],
            manifest_snapshot=row[9],
            permissions_snapshot=row[10],
            previous_deployment_id=row[11],
            runtime_generation=row[12],
            activated_at=row[13],
            error_message=row[14],
            validation_summary=row[15],
        )
