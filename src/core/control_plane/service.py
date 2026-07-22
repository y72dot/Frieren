from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import tomllib
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from src.core.prompts import PromptRegistry

_SENSITIVE_PARTS = {"api_key", "token", "password", "secret", "env", "admin_users"}
_FORBIDDEN_PLUGIN_IMPORTS = {"docker", "napcat", "subprocess", "ctypes"}
_FORBIDDEN_PLUGIN_TEXT = {".env", "docker.sock", "QQNT", "NapCatQQ"}


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
    ) -> None:
        self.bot = bot
        self.connection = connection
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
        self.connection.commit()

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

    def list_plugins(self) -> list[dict[str, Any]]:
        disabled = set(self.bot.config.plugin.disabled_plugins)
        return [
            {"name": plugin.name, "priority": plugin.priority, "enabled": plugin.name not in disabled}
            for plugin in self.bot.plugin_manager.plugins
        ]

    def validate_plugin_candidate(self, candidate: str) -> dict[str, Any]:
        root = self._candidate_path(candidate)
        manifest_path = root / "plugin.toml"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"plugin manifest not found: {candidate}/plugin.toml")
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
        name = str(manifest.get("name", "")).strip()
        version = str(manifest.get("version", "")).strip()
        entrypoint = str(manifest.get("entrypoint", "")).strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name):
            raise ValueError("plugin name must be lowercase snake_case")
        if not version or not entrypoint:
            raise ValueError("plugin manifest requires version and entrypoint")
        entry_path = (root / entrypoint).resolve()
        if root not in entry_path.parents or not entry_path.is_file() or entry_path.suffix != ".py":
            raise ValueError("plugin entrypoint must be a Python file inside candidate")
        violations: list[str] = []
        for source_path in root.rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(source_path))
            except SyntaxError as exc:
                violations.append(f"{source_path.name}: syntax error: {exc.msg}")
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [item.name.split(".", 1)[0] for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".", 1)[0]]
                for imported in names:
                    if imported in _FORBIDDEN_PLUGIN_IMPORTS:
                        violations.append(f"{source_path.name}: forbidden import {imported}")
            for marker in _FORBIDDEN_PLUGIN_TEXT:
                if marker.lower() in source.lower():
                    violations.append(f"{source_path.name}: forbidden resource marker {marker}")
        permissions = manifest.get("permissions", {})
        digest = hashlib.sha256()
        for source_path in sorted(root.rglob("*")):
            if source_path.is_file():
                digest.update(source_path.relative_to(root).as_posix().encode())
                digest.update(source_path.read_bytes())
        return {
            "valid": not violations,
            "name": name,
            "version": version,
            "entrypoint": entrypoint,
            "permissions": permissions,
            "violations": violations,
            "candidate": candidate,
            "sha256": digest.hexdigest(),
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

    def approve_and_apply(self, proposal_id: str, *, approved_by: int) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        if proposal.status != "pending":
            raise ValueError(f"proposal is not pending: {proposal.status}")
        try:
            self._apply(proposal)
        except Exception as exc:
            self._decide(proposal_id, "failed", approved_by, str(exc))
            raise
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
            disabled = set(self.bot.config.plugin.disabled_plugins)
            if payload["enabled"]:
                disabled.discard(payload["name"])
            else:
                disabled.add(payload["name"])
            self.bot.config.plugin.disabled_plugins = sorted(disabled)
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

    def _apply_plugin_install(self, payload: dict[str, Any]) -> None:
        validation = payload["validation"]
        current_validation = self.validate_plugin_candidate(payload["candidate"])
        if not current_validation["valid"] or current_validation["sha256"] != validation["sha256"]:
            raise ValueError("plugin candidate changed after validation")
        root = self._candidate_path(payload["candidate"])
        source = (root / validation["entrypoint"]).resolve()
        target = self.plugin_dir / f"{validation['name']}.py"
        backup = None
        if target.exists():
            backup_dir = self.plugin_dir / ".plugin_backups"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / f"{validation['name']}-{uuid.uuid4().hex}.py"
            shutil.copy2(target, backup)
        self._backups[validation["name"]] = backup
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        shutil.copy2(source, temp)
        os.replace(temp, target)
        self.connection.execute(
            "UPDATE plugin_deployments SET status='superseded' "
            "WHERE name=? AND status='active'",
            (validation["name"],),
        )
        self.connection.execute(
            """INSERT INTO plugin_deployments (
                   deployment_id, name, version, target_path, backup_path,
                   installed_at, status
               ) VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            (
                uuid.uuid4().hex,
                validation["name"],
                validation["version"],
                str(target),
                str(backup) if backup else None,
                int(time.time()),
            ),
        )
        self.connection.commit()

    def _apply_plugin_rollback(self, name: str) -> None:
        row = self.connection.execute(
            """SELECT deployment_id, target_path, backup_path FROM plugin_deployments
               WHERE name=? AND status='active' ORDER BY installed_at DESC LIMIT 1""",
            (name,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"no active deployment for plugin: {name}")
        target = Path(row[1])
        if row[2]:
            shutil.copy2(Path(row[2]), target)
        else:
            target.unlink(missing_ok=True)
        self.connection.execute(
            "UPDATE plugin_deployments SET status='rolled_back' WHERE deployment_id=?",
            (row[0],),
        )
        self.connection.commit()

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
