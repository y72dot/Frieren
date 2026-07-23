from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from src.core.control_plane._deployer import PackageDeployer
from src.core.control_plane._security import SecurityReport, SecurityValidator
from src.core.prompts import PromptRegistry

_SENSITIVE_PARTS = {"api_key", "token", "password", "secret", "env", "admin_users"}


@dataclass(frozen=True)
class ChangeProposal:
    proposal_id: str
    kind: str
    payload_json: str
    risk: str
    status: str
    validation_json: str
    created_by: int | None
    created_at: int
    decided_by: int | None
    decided_at: int | None
    error: str | None

    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    def validation(self) -> dict[str, Any]:
        return json.loads(self.validation_json)

    def to_dict(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        value["payload"] = self.payload()
        value["validation"] = self.validation()
        del value["payload_json"]
        del value["validation_json"]
        return value


class ControlPlane:
    """Proposal-only Agent control surface with separately authorized apply APIs."""

    _COLUMNS = (
        "proposal_id, kind, payload_json, risk, status, validation_json, "
        "created_by, created_at, decided_by, decided_at, error"
    )

    def __init__(
        self,
        bot: Any,
        connection: sqlite3.Connection,
        *,
        prompts_dir: str | Path = "config/prompts",
        candidate_dir: str | Path = "plugins/candidates",
        plugin_dir: str | Path = "plugins",
        coordinator: Any = None,
    ) -> None:
        self.bot = bot
        self.connection = connection
        self._coordinator = coordinator
        self.prompts_dir = Path(prompts_dir).resolve()
        self.candidate_dir = Path(candidate_dir).resolve()
        self.plugin_dir = Path(plugin_dir).resolve()
        self._backups: dict[str, Path | None] = {}
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS control_proposals (
                proposal_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                risk TEXT NOT NULL,
                status TEXT NOT NULL,
                validation_json TEXT NOT NULL,
                created_by INTEGER,
                created_at INTEGER NOT NULL,
                decided_by INTEGER,
                decided_at INTEGER,
                error TEXT
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS plugin_deployments (
                deployment_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                target_path TEXT NOT NULL,
                backup_path TEXT,
                installed_at INTEGER NOT NULL,
                status TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_control_proposals_status "
            "ON control_proposals(status, created_at)"
        )
        self._migrate_schema_v2()
        self._security = SecurityValidator(self.plugin_dir)
        self._deployer = PackageDeployer(
            self.plugin_dir, self.candidate_dir, self.connection
        )

    # ------------------------------------------------------------------
    # schema migration
    # ------------------------------------------------------------------

    def _migrate_schema_v2(self) -> None:
        """Idempotent migration adding package deployment columns."""
        existing = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA table_info(plugin_deployments)"
            ).fetchall()
        }
        new_columns = {
            "deployment_phase": "TEXT NOT NULL DEFAULT 'active'",
            "package_digest": "TEXT NOT NULL DEFAULT ''",
            "manifest_snapshot": "TEXT",
            "permissions_snapshot": "TEXT",
            "previous_deployment_id": "TEXT",
            "runtime_generation": "INTEGER NOT NULL DEFAULT 0",
            "activated_at": "INTEGER NOT NULL DEFAULT 0",
            "error_message": "TEXT",
            "validation_summary": "TEXT",
        }
        for col_name, col_def in new_columns.items():
            if col_name not in existing:
                self.connection.execute(
                    f"ALTER TABLE plugin_deployments ADD COLUMN {col_name} {col_def}"
                )

        # Backfill existing rows
        self.connection.execute(
            "UPDATE plugin_deployments SET deployment_phase='active' "
            "WHERE deployment_phase IS NULL OR deployment_phase=''"
        )
        self.connection.execute(
            "UPDATE plugin_deployments SET status='legacy' "
            "WHERE status='active' "
            "AND (package_digest IS NULL OR package_digest='')"
        )

        # Indexes
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_plugin_deployments_name_status "
            "ON plugin_deployments(name, status)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_plugin_deployments_prev "
            "ON plugin_deployments(previous_deployment_id)"
        )
        self.connection.commit()

    def recover_deployments(self) -> list[str]:
        """Recover incomplete deployments on boot."""
        return self._deployer.recover_on_boot()

    # ------------------------------------------------------------------
    # settings & prompts (unchanged)
    # ------------------------------------------------------------------

    def get_setting(self, path: str) -> Any:
        _reject_sensitive_path(path)
        value = self.bot.config_center.get(path, default=_Missing)
        if value is _Missing:
            raise KeyError(f"unknown setting: {path}")
        return asdict(value) if is_dataclass(value) else deepcopy(value)

    def propose_settings(
        self, changes: dict[str, Any], *, created_by: int | None, reason: str = ""
    ) -> ChangeProposal:
        if not changes:
            raise ValueError("settings proposal has no changes")
        normalized: dict[str, Any] = {}
        for path, value in changes.items():
            _reject_sensitive_path(path)
            current = self.bot.config_center.get(path, default=_Missing)
            if current is _Missing:
                raise KeyError(f"unknown setting: {path}")
            if not _same_config_type(current, value):
                raise TypeError(f"setting type mismatch for {path}")
            normalized[path] = value
        risk = "high" if any(path.startswith(("plugin.", "tools.", "web.")) for path in normalized) else "medium"
        return self._create(
            "settings",
            {"changes": normalized, "reason": reason},
            risk,
            {"valid": True, "changed_paths": sorted(normalized)},
            created_by,
        )

    def get_prompt(self, part: str) -> dict[str, str]:
        path = self._prompt_path(part)
        return {"part": part, "content": path.read_text(encoding="utf-8")}

    def propose_prompt(
        self,
        part: str,
        content: str,
        *,
        version: str,
        created_by: int | None,
        reason: str = "",
    ) -> ChangeProposal:
        self._prompt_path(part)
        if not version.strip():
            raise ValueError("prompt proposal requires a version")
        if not content.strip() or len(content) > 100_000 or "\x00" in content:
            raise ValueError("prompt content is empty, too large, or contains NUL")
        validation = {
            "valid": True,
            "characters": len(content),
            "contains_external_tool_call_markup": "tool_call" in content.lower(),
            "requires_behavior_review": True,
        }
        return self._create(
            "prompt",
            {"part": part, "content": content, "version": version, "reason": reason},
            "high",
            validation,
            created_by,
        )

    # ------------------------------------------------------------------
    # plugin list
    # ------------------------------------------------------------------

    def list_plugins(self) -> list[dict[str, Any]]:
        disabled = set(self.bot.config.plugin.disabled_plugins)
        return [
            {"name": plugin.name, "priority": plugin.priority, "enabled": plugin.name not in disabled}
            for plugin in self.bot.plugin_manager.plugins
        ]

    # ------------------------------------------------------------------
    # plugin validation (delegated to SecurityValidator)
    # ------------------------------------------------------------------

    def validate_plugin_candidate(self, candidate: str) -> dict[str, Any]:
        root = self._candidate_path(candidate)
        report = self._security.validate(root)
        return {
            "valid": report.valid,
            "name": report.name,
            "version": report.version,
            "entrypoint": report.entrypoint,
            "permissions": report.permissions,
            "violations": report.violations,
            "candidate": candidate,
            "sha256": report.sha256,
            "manifest_snapshot": report.manifest_snapshot,
            "file_count": report.file_count,
            "total_size_bytes": report.total_size_bytes,
            "symlinks_detected": report.symlinks_detected,
            "warnings": report.warnings,
        }

    def propose_plugin_install(self, candidate: str, *, created_by: int | None) -> ChangeProposal:
        validation = self.validate_plugin_candidate(candidate)
        if not validation["valid"]:
            raise ValueError("plugin candidate failed validation")
        return self._create(
            "plugin.install",
            {"candidate": candidate, "validation": validation},
            "critical",
            validation,
            created_by,
        )

    def propose_plugin_state(
        self, name: str, enabled: bool, *, created_by: int | None
    ) -> ChangeProposal:
        return self._create(
            "plugin.state",
            {"name": name, "enabled": enabled},
            "high",
            {"valid": True},
            created_by,
        )

    def propose_plugin_rollback(self, name: str, *, created_by: int | None) -> ChangeProposal:
        return self._create(
            "plugin.rollback",
            {"name": name},
            "critical",
            {"valid": True, "requires_backup": True},
            created_by,
        )

    # ------------------------------------------------------------------
    # proposal lifecycle
    # ------------------------------------------------------------------

    async def approve_and_apply(self, proposal_id: str, *, approved_by: int) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        if proposal.status != "pending":
            raise ValueError(f"proposal is not pending: {proposal.status}")

        disabled_before = list(self.bot.config.plugin.disabled_plugins)
        runtime_before = self._runtime_plugin_state(proposal)
        rollback_guard = (
            self._create_rollback_guard(proposal)
            if proposal.kind == "plugin.rollback"
            else None
        )
        try:
            self._apply(proposal)
            if proposal.kind in ("plugin.install", "plugin.rollback", "plugin.state"):
                if self._coordinator is None:
                    raise RuntimeError("plugin deployment coordinator is unavailable")
                op_request = self.create_operation_request(proposal_id)
                report = await self._coordinator.execute(op_request)
                if not report.success:
                    raise RuntimeError(
                        report.error
                        or f"plugin operation failed: {proposal.kind}"
                    )
        except Exception as exc:
            compensation_error = await self._compensate_plugin_operation(
                proposal,
                disabled_before=disabled_before,
                runtime_before=runtime_before,
                rollback_guard=rollback_guard,
            )
            error = str(exc)
            if compensation_error:
                error = f"{error}; compensation failed: {compensation_error}"
            self._decide(proposal_id, "failed", approved_by, error)
            if compensation_error:
                raise RuntimeError(error) from exc
            raise

        self._discard_rollback_guard(rollback_guard)
        self._decide(proposal_id, "applied", approved_by, None)

        result = self.get(proposal_id)
        assert result is not None
        return result

    def reject(self, proposal_id: str, *, decided_by: int, reason: str) -> None:
        self._decide(proposal_id, "rejected", decided_by, reason)

    def get(self, proposal_id: str) -> ChangeProposal | None:
        row = self.connection.execute(
            f"SELECT {self._COLUMNS} FROM control_proposals WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        return ChangeProposal(*row) if row else None

    def list_proposals(self, *, status: str | None = None) -> list[ChangeProposal]:
        sql = f"SELECT {self._COLUMNS} FROM control_proposals"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status=?"
            params = (status,)
        sql += " ORDER BY created_at DESC, proposal_id"
        return [ChangeProposal(*row) for row in self.connection.execute(sql, params)]

    # ------------------------------------------------------------------
    # R2 bridge: create operation request for coordinator
    # ------------------------------------------------------------------

    def create_operation_request(self, proposal_id: str) -> Any:
        """Build an OperationRequest from an approved proposal payload."""
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        if proposal.status not in ("pending", "applied"):
            raise ValueError(f"proposal cannot be applied: {proposal.status}")

        from src.core.control_plane._coordinator import (
            CoordinatorOp,
            OperationRequest,
        )

        payload = proposal.payload()
        if proposal.kind == "plugin.install":
            validation = payload["validation"]
            # Find the deployment record created by _apply_plugin_install
            row = self.connection.execute(
                "SELECT deployment_id FROM plugin_deployments "
                "WHERE name=? AND status='pending_activation' "
                "ORDER BY installed_at DESC LIMIT 1",
                (validation["name"],),
            ).fetchone()
            deployment_id = row[0] if row else ""
            return OperationRequest(
                op=CoordinatorOp.INSTALL,
                plugin_id=validation["name"],
                deployment_id=deployment_id,
                proposal_id=proposal_id,
                version=validation["version"],
                enabled=True,
            )
        if proposal.kind == "plugin.rollback":
            name = payload["name"]
            row = self.connection.execute(
                "SELECT deployment_id, version FROM plugin_deployments "
                "WHERE name=? AND status='pending_activation' "
                "ORDER BY installed_at DESC LIMIT 1",
                (name,),
            ).fetchone()
            deployment_id = row[0] if row else ""
            return OperationRequest(
                op=CoordinatorOp.ROLLBACK,
                plugin_id=name,
                deployment_id=deployment_id,
                proposal_id=proposal_id,
                version=row[1] if row else "",
                enabled=True,
            )
        if proposal.kind == "plugin.state":
            name = payload["name"]
            return OperationRequest(
                op=CoordinatorOp.ENABLE if payload["enabled"] else CoordinatorOp.DISABLE,
                plugin_id=name,
                deployment_id="",
                proposal_id=proposal_id,
                version="",
                enabled=payload["enabled"],
            )
        raise ValueError(f"unsupported proposal kind for operation: {proposal.kind}")

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def _apply(self, proposal: ChangeProposal) -> None:
        payload = proposal.payload()
        if proposal.kind == "settings":
            previous = self.bot.config_center.config
            config = deepcopy(self.bot.config_center.config)
            previous_values = {
                path: self.bot.config_center.get(path)
                for path in payload["changes"]
            }
            for path, value in payload["changes"].items():
                _set_path(config, path, value)
            _validate_effective_config(config)
            try:
                self._activate_config(config, changes=payload["changes"])
            except Exception:
                self._activate_config(previous, changes=previous_values)
                raise
            return
        if proposal.kind == "prompt":
            self._apply_prompt(payload)
            return
        if proposal.kind == "plugin.install":
            self._apply_plugin_install(payload)
            return
        if proposal.kind == "plugin.state":
            config = deepcopy(self.bot.config)
            disabled = set(config.plugin.disabled_plugins)
            if payload["enabled"]:
                disabled.discard(payload["name"])
            else:
                disabled.add(payload["name"])
            config.plugin.disabled_plugins = sorted(disabled)
            self.bot.config_center.replace_config(
                config,
                changes={"plugin.disabled_plugins": config.plugin.disabled_plugins},
            )
            self.bot.config = config
            return
        if proposal.kind == "plugin.rollback":
            self._apply_plugin_rollback(payload["name"])
            return
        raise ValueError(f"unsupported proposal kind: {proposal.kind}")

    def _activate_config(
        self, config: Any, *, changes: dict[str, Any] | None = None
    ) -> None:
        self.bot.config_center.replace_config(config, changes=changes)
        self.bot.config = config
        if self.bot.tool_executor is not None:
            self.bot.tool_executor.default_timeout = config.tools.default_timeout
            self.bot.tool_executor.max_result_bytes = config.tools.max_result_bytes
        if self.bot.scheduler is not None:
            self.bot.scheduler.poll_interval = config.scheduler.poll_interval
            self.bot.scheduler.max_catch_up = config.scheduler.max_catch_up
        self.bot.workspace = None
        self.bot.web_client = None
        self.bot.search_service = None
        self.bot.ensure_capability_services()

    def _apply_prompt(self, payload: dict[str, Any]) -> None:
        part_path = self._prompt_path(payload["part"])
        manifest_path = self.prompts_dir / "manifest.toml"
        old_part = part_path.read_bytes()
        old_manifest = manifest_path.read_bytes()
        manifest_text = old_manifest.decode("utf-8")
        updated_manifest = re.sub(
            r'(?m)^version\s*=\s*"[^"]*"',
            f'version = "{payload["version"]}"',
            manifest_text,
            count=1,
        )
        try:
            _atomic_write(part_path, payload["content"].encode("utf-8"))
            _atomic_write(manifest_path, updated_manifest.encode("utf-8"))
            registry = PromptRegistry.load(self.prompts_dir)
            if registry.version != payload["version"]:
                raise ValueError("prompt manifest version was not updated")
        except Exception:
            _atomic_write(part_path, old_part)
            _atomic_write(manifest_path, old_manifest)
            raise
        self.bot.prompt_registry = registry

    # ------------------------------------------------------------------
    # plugin install / rollback (delegated to PackageDeployer)
    # ------------------------------------------------------------------

    def _apply_plugin_install(self, payload: dict[str, Any]) -> None:
        validation = payload["validation"]
        current = self.validate_plugin_candidate(payload["candidate"])
        if not current["valid"] or current["sha256"] != validation["sha256"]:
            raise ValueError("plugin candidate changed after validation")

        root = self._candidate_path(payload["candidate"])
        plugin_id = validation["name"]

        # Build a lightweight SecurityReport-like object for the deployer
        report = SecurityReport(
            valid=current["valid"],
            name=current["name"],
            version=current["version"],
            entrypoint=current["entrypoint"],
            permissions=current["permissions"],
            violations=current["violations"],
            candidate=current["candidate"],
            sha256=current["sha256"],
            manifest_snapshot=current.get("manifest_snapshot"),
            file_count=current.get("file_count", 0),
            total_size_bytes=current.get("total_size_bytes", 0),
            symlinks_detected=current.get("symlinks_detected", False),
            warnings=current.get("warnings", []),
        )

        # Stage candidate → deploy via state machine
        self._deployer.stage(root, plugin_id)
        self._deployer.deploy(plugin_id, report)
        # deploy() commits internally; phase is FILES_SWITCHED, status=pending_activation

    def _apply_plugin_rollback(self, name: str) -> None:
        self._deployer.rollback(name)
        # rollback() handles status/phases internally:
        # marks current as rolled_back, reactivates previous as pending_activation

    def _runtime_plugin_state(self, proposal: ChangeProposal) -> dict[str, Any]:
        plugin_id = self._proposal_plugin_id(proposal)
        if not plugin_id or not hasattr(self.bot, "plugin_runtime"):
            return {"plugin_id": plugin_id, "enabled": False, "version": ""}
        plugin = self.bot.plugin_runtime.get_plugin(plugin_id)
        enabled = plugin_id in self.bot.plugin_runtime.snapshot.plugin_ids
        return {
            "plugin_id": plugin_id,
            "enabled": enabled,
            "version": plugin.manifest.version if plugin is not None else "",
        }

    @staticmethod
    def _proposal_plugin_id(proposal: ChangeProposal) -> str:
        payload = proposal.payload()
        if proposal.kind == "plugin.install":
            return str(payload["validation"]["name"])
        if proposal.kind in ("plugin.rollback", "plugin.state"):
            return str(payload["name"])
        return ""

    def _create_rollback_guard(
        self, proposal: ChangeProposal
    ) -> dict[str, Any] | None:
        plugin_id = self._proposal_plugin_id(proposal)
        if not plugin_id:
            return None
        target = self.plugin_dir / plugin_id
        guard = self.plugin_dir / ".staging" / f".rollback-{proposal.proposal_id}"
        if guard.exists():
            shutil.rmtree(guard)
        if target.is_dir():
            guard.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, guard)

        rows = self.connection.execute(
            "SELECT deployment_id, status, deployment_phase, backup_path, "
            "error_message FROM plugin_deployments WHERE name=?",
            (plugin_id,),
        ).fetchall()
        active = self.connection.execute(
            "SELECT deployment_id, backup_path FROM plugin_deployments "
            "WHERE name=? AND status IN ('active', 'pending_activation', 'pending') "
            "ORDER BY installed_at DESC LIMIT 1",
            (plugin_id,),
        ).fetchone()
        return {
            "plugin_id": plugin_id,
            "target": target,
            "guard": guard,
            "target_existed": target.is_dir(),
            "rows": rows,
            "active_backup": active[1] if active else None,
        }

    def _restore_rollback_guard(self, guard: dict[str, Any]) -> None:
        target = Path(guard["target"])
        backup = Path(guard["guard"])
        active_backup = (
            Path(guard["active_backup"]) if guard.get("active_backup") else None
        )

        if target.is_dir():
            if active_backup is not None and not active_backup.exists():
                active_backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, active_backup)
            else:
                shutil.rmtree(target)
        if guard["target_existed"] and backup.is_dir():
            os.replace(backup, target)

        for deployment_id, status, phase, backup_path, error_message in guard["rows"]:
            self.connection.execute(
                "UPDATE plugin_deployments SET status=?, deployment_phase=?, "
                "backup_path=?, error_message=? WHERE deployment_id=?",
                (status, phase, backup_path, error_message, deployment_id),
            )
        self.connection.commit()

    @staticmethod
    def _discard_rollback_guard(guard: dict[str, Any] | None) -> None:
        if guard is None:
            return
        path = Path(guard["guard"])
        if path.is_dir():
            shutil.rmtree(path)

    async def _compensate_plugin_operation(
        self,
        proposal: ChangeProposal,
        *,
        disabled_before: list[str],
        runtime_before: dict[str, Any],
        rollback_guard: dict[str, Any] | None,
    ) -> str | None:
        if not proposal.kind.startswith("plugin."):
            return None

        plugin_id = runtime_before["plugin_id"]
        try:
            if proposal.kind == "plugin.install":
                row = self.connection.execute(
                    "SELECT 1 FROM plugin_deployments "
                    "WHERE name=? AND status='pending_activation' "
                    "ORDER BY installed_at DESC LIMIT 1",
                    (plugin_id,),
                ).fetchone()
                if row is not None:
                    self._deployer.rollback(plugin_id)
            elif proposal.kind == "plugin.state":
                config = deepcopy(self.bot.config)
                config.plugin.disabled_plugins = list(disabled_before)
                self.bot.config_center.replace_config(
                    config,
                    changes={"plugin.disabled_plugins": list(disabled_before)},
                )
                self.bot.config = config
            elif proposal.kind == "plugin.rollback" and rollback_guard is not None:
                self._restore_rollback_guard(rollback_guard)

            if self._coordinator is not None:
                report = await self._coordinator.reconcile(
                    plugin_id,
                    expected_enabled=bool(runtime_before["enabled"]),
                    expected_version=str(runtime_before["version"]),
                )
                if not report.success:
                    return report.error or "runtime reconciliation failed"
            return None
        except Exception as exc:
            logger.opt(exception=True).error(
                f"Plugin operation compensation failed for '{plugin_id}'"
            )
            return str(exc)
        finally:
            self._discard_rollback_guard(rollback_guard)

    # ------------------------------------------------------------------
    # proposal helpers
    # ------------------------------------------------------------------

    def _create(
        self,
        kind: str,
        payload: dict[str, Any],
        risk: str,
        validation: dict[str, Any],
        created_by: int | None,
    ) -> ChangeProposal:
        proposal_id = uuid.uuid4().hex
        self.connection.execute(
            """INSERT INTO control_proposals (
                   proposal_id, kind, payload_json, risk, status, validation_json,
                   created_by, created_at, decided_by, decided_at, error
               ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, NULL, NULL, NULL)""",
            (
                proposal_id,
                kind,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                risk,
                json.dumps(validation, ensure_ascii=False, separators=(",", ":")),
                created_by,
                int(time.time()),
            ),
        )
        self.connection.commit()
        result = self.get(proposal_id)
        assert result is not None
        return result

    def _decide(
        self, proposal_id: str, status: str, decided_by: int, error: str | None
    ) -> None:
        self.connection.execute(
            """UPDATE control_proposals SET status=?, decided_by=?, decided_at=?, error=?
               WHERE proposal_id=?""",
            (status, decided_by, int(time.time()), error[:2000] if error else None, proposal_id),
        )
        self.connection.commit()

    def _prompt_path(self, part: str) -> Path:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", part):
            raise ValueError("invalid prompt part name")
        path = (self.prompts_dir / f"{part}.md").resolve()
        if self.prompts_dir not in path.parents or not path.is_file():
            raise FileNotFoundError(f"prompt part not found: {part}")
        return path

    def _candidate_path(self, candidate: str) -> Path:
        root = (self.candidate_dir / candidate).resolve()
        if self.candidate_dir not in root.parents or not root.is_dir():
            raise ValueError("plugin candidate is outside candidate directory")
        return root


class _MissingType:
    pass


_Missing = _MissingType()


def _reject_sensitive_path(path: str) -> None:
    if any(part.lower() in _SENSITIVE_PARTS for part in path.split(".")):
        raise PermissionError(f"sensitive setting is not exposed: {path}")


def _same_config_type(current: Any, value: Any) -> bool:
    if isinstance(current, bool):
        return isinstance(value, bool)
    if isinstance(current, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(current, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(current))


def _set_path(root: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    target = root
    for part in parts[:-1]:
        target = target[part] if isinstance(target, dict) else getattr(target, part)
    if isinstance(target, dict):
        target[parts[-1]] = value
    else:
        setattr(target, parts[-1], value)


def _validate_effective_config(config: Any) -> None:
    if config.tools.default_timeout <= 0 or config.tools.max_result_bytes <= 0:
        raise ValueError("tool limits must be positive")
    if config.scheduler.poll_interval <= 0 or config.scheduler.max_catch_up <= 0:
        raise ValueError("scheduler limits must be positive")
    if config.workspace.max_file_size <= 0 or config.workspace.max_read_size <= 0:
        raise ValueError("workspace limits must be positive")
    if config.web.timeout <= 0 or config.web.max_response_bytes <= 0:
        raise ValueError("web limits must be positive")


def _atomic_write(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(name, path)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
