"""Plugin manifest model, TOML parsing, and strict validation.

A plugin's identity, version, entrypoint, SDK compatibility, dependencies,
and permissions are declared once in ``plugin.toml``.  This module parses
that file into a frozen :class:`PluginManifest` and collects every
validation error before raising.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ManifestError(Exception):
    """Base exception for manifest-related errors."""


class ManifestParseError(ManifestError):
    """TOML parsing or file I/O failure."""


class ManifestValidationError(ManifestError):
    """Semantic validation failure.

    The ``errors`` attribute contains every validation message collected
    so the caller can report all problems at once.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestPermissions:
    """Declared plugin permissions."""

    qq: list[str] = field(default_factory=list)
    storage: list[str] = field(default_factory=list)
    scheduler: bool = False
    network: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ManifestConfig:
    """Plugin configuration schema reference."""

    schema: str = ""  # "module:Class"
    storage_schema_version: int = 0


@dataclass(frozen=True)
class PluginManifest:
    """Immutable parsed manifest for a single plugin."""

    id: str
    version: str
    entrypoint: str
    sdk: str
    name: str = ""
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    permissions: ManifestPermissions = field(default_factory=ManifestPermissions)
    config: ManifestConfig | None = None

    def __post_init__(self) -> None:
        if not self.name:
            object.__setattr__(self, "name", self.id)


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"
)
_ENTRYPOINT_RE = re.compile(
    r"^([a-zA-Z_][a-zA-Z0-9_.]*):([a-zA-Z_][a-zA-Z0-9_]*)$"
)

# Known TOML keys per section (strict mode).
_KNOWN_TOP_KEYS = {"plugin", "dependencies", "permissions", "config"}
_KNOWN_PLUGIN_KEYS = {"id", "name", "version", "entrypoint", "sdk", "description"}
_KNOWN_DEPENDENCIES_KEYS = {"plugins"}
_KNOWN_PERMISSIONS_KEYS = {"qq", "storage", "scheduler", "network"}
_KNOWN_CONFIG_KEYS = {"schema", "storage_schema_version"}

# Permission name whitelists.
_KNOWN_QQ_PERMISSIONS = {"message.send", "message.react", "group.manage"}
_KNOWN_STORAGE_PERMISSIONS = {"plugin", "plugin.read", "plugin.write"}
_KNOWN_NETWORK_PERMISSIONS = {"http"}


# ---------------------------------------------------------------------------
# Validators (return list of error strings)
# ---------------------------------------------------------------------------


def _validate_plugin_id(plugin_id: str) -> list[str]:
    if not _PLUGIN_ID_RE.match(plugin_id):
        return [
            f"Invalid plugin.id '{plugin_id}': must be lowercase snake_case "
            f"(letters, digits, underscores; start with a letter)"
        ]
    return []


def _validate_version(version: str) -> list[str]:
    if not _VERSION_RE.match(version):
        return [
            f"Invalid plugin.version '{version}': must be semver (e.g. 1.0.0)"
        ]
    return []


def _validate_entrypoint(entrypoint: str) -> list[str]:
    errors: list[str] = []
    if not entrypoint:
        errors.append("plugin.entrypoint must not be empty")
        return errors
    if ".." in entrypoint:
        errors.append(
            f"plugin.entrypoint '{entrypoint}' contains disallowed path traversal '..'"
        )
    m = _ENTRYPOINT_RE.match(entrypoint)
    if m is None:
        errors.append(
            f"Invalid plugin.entrypoint '{entrypoint}': must be 'module.path:attr' format"
        )
    return errors


def _validate_permissions(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for perm in raw.get("qq", []):
        if perm not in _KNOWN_QQ_PERMISSIONS:
            errors.append(
                f"Unknown qq permission '{perm}'; known: "
                + ", ".join(sorted(_KNOWN_QQ_PERMISSIONS))
            )
    for perm in raw.get("storage", []):
        if perm not in _KNOWN_STORAGE_PERMISSIONS:
            errors.append(
                f"Unknown storage permission '{perm}'; known: "
                + ", ".join(sorted(_KNOWN_STORAGE_PERMISSIONS))
            )
    for perm in raw.get("network", []):
        if perm not in _KNOWN_NETWORK_PERMISSIONS:
            errors.append(
                f"Unknown network permission '{perm}'; known: "
                + ", ".join(sorted(_KNOWN_NETWORK_PERMISSIONS))
            )
    scheduler = raw.get("scheduler")
    if scheduler is not None and not isinstance(scheduler, bool):
        errors.append(
            f"permissions.scheduler must be a boolean, got {type(scheduler).__name__}"
        )
    return errors


def _validate_unknown_fields(raw: dict[str, Any]) -> list[str]:
    """Recursively check for unknown top-level and section keys."""
    errors: list[str] = []

    for key in raw:
        if key not in _KNOWN_TOP_KEYS:
            errors.append(
                f"Unknown top-level section '[{key}]'; known: "
                + ", ".join(sorted(_KNOWN_TOP_KEYS))
            )

    plugin_raw = raw.get("plugin", {})
    if isinstance(plugin_raw, dict):
        for key in plugin_raw:
            if key not in _KNOWN_PLUGIN_KEYS:
                errors.append(
                    f"Unknown field in [plugin]: '{key}'; known: "
                    + ", ".join(sorted(_KNOWN_PLUGIN_KEYS))
                )

    deps_raw = raw.get("dependencies", {})
    if isinstance(deps_raw, dict):
        for key in deps_raw:
            if key not in _KNOWN_DEPENDENCIES_KEYS:
                errors.append(
                    f"Unknown field in [dependencies]: '{key}'; known: "
                    + ", ".join(sorted(_KNOWN_DEPENDENCIES_KEYS))
                )

    perms_raw = raw.get("permissions", {})
    if isinstance(perms_raw, dict):
        for key in perms_raw:
            if key not in _KNOWN_PERMISSIONS_KEYS:
                errors.append(
                    f"Unknown field in [permissions]: '{key}'; known: "
                    + ", ".join(sorted(_KNOWN_PERMISSIONS_KEYS))
                )

    config_raw = raw.get("config", {})
    if isinstance(config_raw, dict):
        for key in config_raw:
            if key not in _KNOWN_CONFIG_KEYS:
                errors.append(
                    f"Unknown field in [config]: '{key}'; known: "
                    + ", ".join(sorted(_KNOWN_CONFIG_KEYS))
                )

    return errors


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_manifest(path: str | Path) -> PluginManifest:
    """Parse and validate a ``plugin.toml`` file.

    Parameters
    ----------
    path:
        Absolute path to the ``plugin.toml`` file.

    Returns
    -------
    PluginManifest
        Frozen, validated manifest.

    Raises
    ------
    ManifestParseError
        If the file cannot be read or the TOML is invalid.
    ManifestValidationError
        If any validation rule is violated.  All errors are collected
        and reported together.
    """
    path = Path(path)

    # -- read + parse TOML --------------------------------------------------
    try:
        with open(path, "rb") as fh:
            raw: dict[str, Any] = tomllib.load(fh)
    except FileNotFoundError:
        raise ManifestParseError(f"plugin.toml not found at {path}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ManifestParseError(f"TOML parse error in {path}: {exc}") from exc

    # -- collect all errors -------------------------------------------------
    errors: list[str] = []

    # Unknown field detection (strict mode).
    errors.extend(_validate_unknown_fields(raw))

    # Required fields.
    plugin_raw = raw.get("plugin", {})
    if not isinstance(plugin_raw, dict):
        errors.append("[plugin] section must be a TOML table")
        raise ManifestValidationError(errors)

    for key in ("id", "version", "entrypoint", "sdk"):
        if key not in plugin_raw:
            errors.append(f"[plugin] missing required field: {key}")

    plugin_id = str(plugin_raw.get("id", ""))
    version = str(plugin_raw.get("version", ""))
    entrypoint = str(plugin_raw.get("entrypoint", ""))
    sdk = str(plugin_raw.get("sdk", ""))

    # Validate individual fields (only if present – missing is caught above).
    if plugin_id:
        errors.extend(_validate_plugin_id(plugin_id))
    if version:
        errors.extend(_validate_version(version))
    if entrypoint:
        errors.extend(_validate_entrypoint(entrypoint))

    # Permissions.
    perms_raw = raw.get("permissions", {})
    if isinstance(perms_raw, dict):
        errors.extend(_validate_permissions(perms_raw))

    # Dependencies type check.
    deps_raw = raw.get("dependencies", {})
    if isinstance(deps_raw, dict):
        plugins_deps = deps_raw.get("plugins", [])
        if not isinstance(plugins_deps, list):
            errors.append("dependencies.plugins must be a list of strings")

    if errors:
        raise ManifestValidationError(errors)

    # -- build result -------------------------------------------------------
    name = str(plugin_raw.get("name", "")) or plugin_id
    description = str(plugin_raw.get("description", ""))

    deps_list: list[str] = []
    if isinstance(deps_raw, dict):
        raw_deps = deps_raw.get("plugins", [])
        if isinstance(raw_deps, list):
            deps_list = [str(d) for d in raw_deps]

    perms = ManifestPermissions()
    if isinstance(perms_raw, dict):
        perms = ManifestPermissions(
            qq=[str(p) for p in perms_raw.get("qq", [])],
            storage=[str(p) for p in perms_raw.get("storage", [])],
            scheduler=bool(perms_raw.get("scheduler", False)),
            network=[str(p) for p in perms_raw.get("network", [])],
        )

    config: ManifestConfig | None = None
    config_raw = raw.get("config", {})
    if isinstance(config_raw, dict) and (config_raw.get("schema") or config_raw.get("storage_schema_version")):
        config = ManifestConfig(
            schema=str(config_raw.get("schema", "")),
            storage_schema_version=int(config_raw.get("storage_schema_version", 0)),
        )

    return PluginManifest(
        id=plugin_id,
        name=name,
        version=version,
        entrypoint=entrypoint,
        sdk=sdk,
        description=description,
        dependencies=deps_list,
        permissions=perms,
        config=config,
    )
