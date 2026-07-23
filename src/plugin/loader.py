"""Plugin discovery without code execution.

The loader system identifies what plugins are available on disk **without**
importing or executing any plugin code.  Discovery produces immutable
:class:`Candidate` objects that carry parsed manifests and loader-type
metadata.  Actual import and registration happens later in
:class:`PluginManager`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from loguru import logger

from src.plugin.manifest import (
    ManifestError,
    PluginManifest,
    parse_manifest,
)


class LoaderType(Enum):
    """How a plugin was discovered."""
    PACKAGE = auto()   # directory with plugin.toml


@dataclass(frozen=True)
class Candidate:
    """A discovered but **not yet loaded** plugin.

    Discovery identifies what is available on disk.  Loading (import,
    instantiation, registration) happens in a later phase.
    """

    plugin_id: str
    """Stable identity from the manifest."""

    path: Path
    """Absolute path to the plugin directory (PACKAGE) or .py file (LEGACY)."""

    manifest: PluginManifest
    """Parsed manifest (synthetic for legacy plugins)."""

    loader_type: LoaderType
    """How this plugin was discovered."""

    source_module: str = ""
    """Dotted module name derived from the entrypoint (empty during discovery)."""


# ---------------------------------------------------------------------------
# PackageLoader
# ---------------------------------------------------------------------------


class PackageLoader:
    """Scans directories for plugin packages (directory + ``plugin.toml``).

    Does **not** import or execute any plugin code.
    """

    def scan(self, root_dir: Path) -> list[Candidate]:
        """Walk immediate subdirectories of *root_dir*.

        Each subdirectory that contains a ``plugin.toml`` produces one
        :class:`Candidate`.
        """
        candidates: list[Candidate] = []

        if not root_dir.is_dir():
            logger.debug(f"PackageLoader: directory not found: {root_dir}")
            return candidates

        for entry in sorted(root_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue

            toml_path = entry / "plugin.toml"
            if not toml_path.is_file():
                continue

            try:
                manifest = parse_manifest(toml_path)
            except ManifestError as exc:
                logger.warning(
                    f"PackageLoader: skipping {entry.name}: {exc}"
                )
                continue

            # Check entrypoint path containment.
            path_errors = _check_entrypoint_path(entry, manifest)
            if path_errors:
                logger.warning(
                    f"PackageLoader: skipping {entry.name}: "
                    + "; ".join(path_errors)
                )
                continue

            candidates.append(
                Candidate(
                    plugin_id=manifest.id,
                    path=entry.resolve(),
                    manifest=manifest,
                    loader_type=LoaderType.PACKAGE,
                )
            )
            logger.debug(
                f"PackageLoader: found {manifest.id} v{manifest.version} "
                f"at {entry}"
            )

        return candidates


def _check_entrypoint_path(
    plugin_root: Path, manifest: PluginManifest
) -> list[str]:
    """Verify the entrypoint module path stays within *plugin_root*."""
    from src.plugin.manifest import _ENTRYPOINT_RE

    m = _ENTRYPOINT_RE.match(manifest.entrypoint)
    if m is None:
        return []  # syntax error – caught by manifest validation

    module_path = m.group(1).replace(".", "/") + ".py"
    resolved = (plugin_root / module_path).resolve()
    try:
        resolved.relative_to(plugin_root.resolve())
    except ValueError:
        return [
            f"Entrypoint '{manifest.entrypoint}' resolves to {resolved}, "
            f"which is outside plugin root {plugin_root}"
        ]
    return []


# ---------------------------------------------------------------------------
# top-level discovery
# ---------------------------------------------------------------------------


def discover_candidates(plugin_dirs: list[str]) -> list[Candidate]:
    """Discover all plugin candidates from configured directories.

    Uses :class:`PackageLoader` to scan each directory.
    De-duplicates by ``plugin_id`` (first wins).
    Results are sorted alphabetically by ``plugin_id``.

    Returns
    -------
    list[Candidate]
        Flat list of unique candidates sorted by ``plugin_id``.
    """
    package_loader = PackageLoader()

    seen: dict[str, Candidate] = {}

    for dir_name in plugin_dirs:
        path = Path(dir_name).resolve()
        if not path.is_dir():
            logger.warning(f"Plugin directory not found: {dir_name}")
            continue

        for candidate in package_loader.scan(path):
            if candidate.plugin_id in seen:
                logger.warning(
                    f"Duplicate plugin_id '{candidate.plugin_id}' "
                    f"(first at {seen[candidate.plugin_id].path}); skipping"
                )
                continue
            seen[candidate.plugin_id] = candidate

    result = sorted(seen.values(), key=lambda c: c.plugin_id)
    logger.info(
        f"Discovery complete: {len(result)} candidate(s) found "
        f"({sum(1 for c in result if c.loader_type == LoaderType.PACKAGE)} "
        f"package)"
    )
    return result
