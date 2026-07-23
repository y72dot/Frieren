"""Security validator for plugin candidate packages.

Extracted from the inline validation previously in ControlPlane.validate_plugin_candidate().
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.plugin.manifest import (
    ManifestError,
    ManifestParseError,
    parse_manifest,
)

_FORBIDDEN_PLUGIN_IMPORTS = {"docker", "napcat", "subprocess", "ctypes"}
_FORBIDDEN_PLUGIN_TEXT = {".env", "docker.sock", "QQNT", "NapCatQQ"}

_MAX_FILES = 200
_MAX_FILE_BYTES = 1_048_576  # 1 MB
_MAX_TOTAL_BYTES = 10_485_760  # 10 MB


@dataclass(frozen=True)
class SecurityReport:
    """Immutable validation result for a plugin candidate directory."""

    valid: bool
    name: str
    version: str
    entrypoint: str
    permissions: dict
    violations: list[str] = field(default_factory=list)
    candidate: str = ""
    sha256: str = ""
    manifest_snapshot: dict | None = None
    file_count: int = 0
    total_size_bytes: int = 0
    symlinks_detected: bool = False
    warnings: list[str] = field(default_factory=list)


class SecurityValidator:
    """Validates a plugin candidate directory for safety and correctness."""

    def __init__(self, plugin_dir: Path) -> None:
        self._plugin_dir = plugin_dir.resolve()

    def validate(self, candidate_root: Path) -> SecurityReport:
        """Run all security and correctness checks on *candidate_root*."""
        root = candidate_root.resolve()
        warnings: list[str] = []
        violations: list[str] = []

        # ------------------------------------------------------------------
        # Structural checks
        # ------------------------------------------------------------------
        manifest_path = root / "plugin.toml"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"plugin manifest not found: {root}/plugin.toml"
            )

        # Symlink detection
        symlinks_detected = False
        for p in root.rglob("*"):
            if p.is_symlink():
                symlinks_detected = True
                violations.append(f"symlink detected: {p.relative_to(root)}")

        # File limits
        file_paths = sorted(
            p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
        )
        file_count = len(file_paths)
        total_size = sum(p.stat().st_size for p in file_paths)

        if file_count > _MAX_FILES:
            violations.append(
                f"too many files: {file_count} (max {_MAX_FILES})"
            )

        for fp in file_paths:
            try:
                size = fp.stat().st_size
            except OSError:
                violations.append(f"cannot stat file: {fp.relative_to(root)}")
                continue
            if size > _MAX_FILE_BYTES:
                violations.append(
                    f"file too large: {fp.relative_to(root)} ({size} bytes)"
                )

        if total_size > _MAX_TOTAL_BYTES:
            violations.append(
                f"total size too large: {total_size} bytes (max {_MAX_TOTAL_BYTES})"
            )

        # Path traversal rejection
        for p in root.rglob("*"):
            try:
                p.resolve()
            except (OSError, RuntimeError):
                violations.append(f"path resolution error: {p}")
                continue
            if root not in p.resolve().parents and p.resolve() != root:
                violations.append(f"path traversal detected: {p.relative_to(root)}")

        # ------------------------------------------------------------------
        # Manifest
        # ------------------------------------------------------------------
        try:
            manifest = parse_manifest(manifest_path)
        except ManifestParseError as exc:
            if "not found" in str(exc):
                raise FileNotFoundError(
                    f"plugin manifest not found: {root}/plugin.toml"
                ) from exc
            violations.append(f"manifest parse error: {exc}")
            return SecurityReport(
                valid=False,
                name="",
                version="",
                entrypoint="",
                permissions={},
                violations=violations,
                candidate=str(root),
                symlinks_detected=symlinks_detected,
                file_count=file_count,
                total_size_bytes=total_size,
                warnings=warnings,
            )
        except ManifestError as exc:
            violations.append(f"manifest parse error: {exc}")
            return SecurityReport(
                valid=False,
                name="",
                version="",
                entrypoint="",
                permissions={},
                violations=violations,
                candidate=str(root),
                symlinks_detected=symlinks_detected,
                file_count=file_count,
                total_size_bytes=total_size,
                warnings=warnings,
            )

        name = manifest.id
        version = manifest.version
        entrypoint = manifest.entrypoint

        # Entrypoint containment
        if entrypoint:
            module_name = entrypoint.partition(":")[0]
            package_prefixes = (
                f"{self._plugin_dir.name}.{name}.",
                f"{name}.",
            )
            relative_module = next(
                (
                    module_name.removeprefix(prefix)
                    for prefix in package_prefixes
                    if module_name.startswith(prefix)
                ),
                "",
            )
            entry_path = (
                root / (relative_module.replace(".", "/") + ".py")
            ).resolve()
            if (
                not relative_module
                or root not in entry_path.parents
                or not entry_path.is_file()
            ):
                violations.append(
                    "plugin entrypoint must resolve to a Python file inside "
                    f"candidate package: {entrypoint}"
                )

        permissions = asdict(manifest.permissions)

        # ------------------------------------------------------------------
        # AST security
        # ------------------------------------------------------------------
        for source_path in root.rglob("*.py"):
            if source_path.is_symlink():
                continue
            try:
                source = source_path.read_text(encoding="utf-8")
            except Exception:
                violations.append(f"{source_path.name}: cannot read source")
                continue
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

        # ------------------------------------------------------------------
        # SHA-256 digest
        # ------------------------------------------------------------------
        digest = hashlib.sha256()
        for fp in sorted(root.rglob("*")):
            if fp.is_file() and not fp.is_symlink():
                digest.update(fp.relative_to(root).as_posix().encode())
                digest.update(fp.read_bytes())

        # ------------------------------------------------------------------
        # Manifest snapshot
        # ------------------------------------------------------------------
        manifest_snapshot = asdict(manifest)

        return SecurityReport(
            valid=not violations,
            name=name,
            version=version,
            entrypoint=entrypoint,
            permissions=permissions,
            violations=violations,
            candidate=str(root),
            sha256=digest.hexdigest(),
            manifest_snapshot=manifest_snapshot,
            file_count=file_count,
            total_size_bytes=total_size,
            symlinks_detected=symlinks_detected,
            warnings=warnings,
        )
