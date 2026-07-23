"""Dependency topology resolution and SDK version compatibility.

Resolves a flat list of :class:`Candidate` objects into a valid load
order, filtering out candidates whose SDK constraints are not satisfied
or whose dependencies cannot be resolved (missing or circular).
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from loguru import logger

from src.plugin.manifest import ManifestError

# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------


class TopologyError(ManifestError):
    """Base for dependency resolution errors."""


class SdkConstraintError(TopologyError):
    """An SDK version constraint string could not be parsed."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


class CycleDetectedError(TopologyError):
    """A circular dependency was found among plugins."""

    def __init__(self, plugin_ids: list[str]) -> None:
        self.plugin_ids = plugin_ids
        super().__init__(
            f"Circular dependency detected: {' -> '.join(plugin_ids)}"
        )


class MissingDependencyError(TopologyError):
    """A declared dependency does not exist in the discovered set."""

    def __init__(self, plugin_id: str, missing: str) -> None:
        self.plugin_id = plugin_id
        self.missing = missing
        super().__init__(
            f"Plugin '{plugin_id}' depends on '{missing}', which is not found"
        )


# ---------------------------------------------------------------------------
# SdkConstraint
# ---------------------------------------------------------------------------


def _parse_version_tuple(version: str) -> tuple[int, int, int]:
    """Parse ``"1.2.3"`` into ``(1, 2, 3)``, ignoring pre-release/build."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if m is None:
        raise SdkConstraintError([f"Cannot parse version: {version}"])
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


_SDK_CONSTRAINT_RE = re.compile(
    r"^(>=|<=|>|<|==)\s*(\d+)\.(\d+)(?:\.(\d+))?$"
)


@dataclass(frozen=True)
class SdkConstraint:
    """A parsed SDK version constraint like ``>=1.0,<2.0``.

    Multiple comma-separated constraints use AND logic (all must be
    satisfied).  The special value ``"*"`` means any version.

    Use :meth:`parse` to create an instance from a raw string.
    """

    constraints: tuple[tuple[str, tuple[int, int, int]], ...] = ()
    # Each element: (operator, (major, minor, patch))
    wildcard: bool = False

    @classmethod
    def parse(cls, raw: str) -> SdkConstraint:
        """Parse a constraint string like ``">=1.0,<2.0"``.

        Returns
        -------
        SdkConstraint

        Raises
        ------
        SdkConstraintError
            If the string cannot be parsed.
        """
        raw = raw.strip()
        if raw == "*":
            return cls(wildcard=True)

        if not raw:
            raise SdkConstraintError(["SDK constraint must not be empty"])

        parts = [p.strip() for p in raw.split(",") if p.strip()]
        errors: list[str] = []
        parsed: list[tuple[str, tuple[int, int, int]]] = []

        for part in parts:
            m = _SDK_CONSTRAINT_RE.match(part)
            if m is None:
                errors.append(
                    f"Invalid constraint '{part}': expected opX.Y.Z "
                    f"(e.g. '>=1.0.0' or '>=1.0')"
                )
            else:
                op = m.group(1)
                ver = (
                    int(m.group(2)),
                    int(m.group(3)),
                    int(m.group(4)) if m.group(4) is not None else 0,
                )
                parsed.append((op, ver))

        if errors:
            raise SdkConstraintError(errors)

        return cls(constraints=tuple(parsed))

    def check(self, sdk_version: str) -> bool:
        """Return ``True`` if *sdk_version* satisfies all constraints.

        A wildcard (``"*"``) always returns ``True``.
        """
        if self.wildcard:
            return True
        if not self.constraints:
            return True
        sdk = _parse_version_tuple(sdk_version)
        for op, constraint_ver in self.constraints:
            if not _version_cmp(sdk, op, constraint_ver):
                return False
        return True


def _version_cmp(
    a: tuple[int, int, int], op: str, b: tuple[int, int, int]
) -> bool:
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "==":
        return a == b
    return False


# ---------------------------------------------------------------------------
# TopologyResolver
# ---------------------------------------------------------------------------


@dataclass
class TopologyResult:
    """Result of dependency resolution."""

    sorted_candidates: list  # list[Candidate]
    skipped: list  # list[tuple[Candidate, str]]


class TopologyResolver:
    """Resolves plugin dependencies into a valid load order."""

    def __init__(self, candidates: list, sdk_version: str) -> None:
        self._candidates = candidates
        self._sdk_version = sdk_version
        self._by_id: dict[str, object] = {c.plugin_id: c for c in candidates}

    def resolve(self) -> TopologyResult:
        """Sort candidates by dependency order, skipping incompatible ones.

        1. Filter by SDK compatibility.
        2. Check dependency existence.
        3. Kahn's algorithm for topological sort.
        4. Detect cycles in remaining nodes.
        """
        skipped: list = []

        # Step 1: SDK compatibility filter.
        compatible: list = []
        for c in self._candidates:
            try:
                constraint = SdkConstraint.parse(c.manifest.sdk)
            except SdkConstraintError as exc:
                skipped.append(
                    (c, f"Bad SDK constraint '{c.manifest.sdk}': {exc}")
                )
                continue
            if not constraint.check(self._sdk_version):
                skipped.append(
                    (
                        c,
                        f"SDK {self._sdk_version} does not satisfy "
                        f"'{c.manifest.sdk}'",
                    )
                )
                continue
            compatible.append(c)

        # Step 2: dependency existence check.
        valid: list = []
        for c in compatible:
            missing = [
                d
                for d in c.manifest.dependencies
                if d != c.plugin_id and d not in self._by_id
            ]
            if missing:
                skipped.append(
                    (
                        c,
                        f"Missing dependencies: {', '.join(missing)}",
                    )
                )
                continue
            valid.append(c)

        # Step 3: Kahn's algorithm for topological sort.
        valid_ids = {c.plugin_id for c in valid}
        valid_by_id = {c.plugin_id: c for c in valid}

        # Build adjacency: for each candidate's dependencies,
        # dependency → candidate (dependency must load first).
        adj: dict[str, list[str]] = {c.plugin_id: [] for c in valid}
        in_degree: dict[str, int] = {c.plugin_id: 0 for c in valid}

        for c in valid:
            for dep in c.manifest.dependencies:
                if dep == c.plugin_id:
                    logger.warning(
                        f"Plugin '{c.plugin_id}' declares a self-dependency; ignored"
                    )
                    continue
                if dep not in valid_ids:
                    continue  # should not happen after step 2, but be safe
                adj[dep].append(c.plugin_id)
                in_degree[c.plugin_id] += 1

        # Kahn's: start with nodes of in-degree 0.
        queue: deque[str] = deque(
            pid for pid, deg in in_degree.items() if deg == 0
        )
        sorted_ids: list[str] = []

        while queue:
            pid = queue.popleft()
            sorted_ids.append(pid)
            for neighbour in adj.get(pid, []):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        # Step 4: cycle detection.
        if len(sorted_ids) != len(valid):
            remaining = [pid for pid, deg in in_degree.items() if deg > 0]
            for pid in remaining:
                skipped.append(
                    (
                        valid_by_id[pid],
                        f"Circular dependency involving '{pid}'",
                    )
                )
            # Keep only the successfully sorted ones.
            sorted_candidates = [
                valid_by_id[pid] for pid in sorted_ids if pid in valid_by_id
            ]
            return TopologyResult(
                sorted_candidates=sorted_candidates,
                skipped=skipped,
            )

        sorted_candidates = [valid_by_id[pid] for pid in sorted_ids]
        return TopologyResult(sorted_candidates=sorted_candidates, skipped=skipped)


# ---------------------------------------------------------------------------
# top-level convenience
# ---------------------------------------------------------------------------


def resolve_candidates(
    candidates: list,
    sdk_version: str,
) -> tuple[list, list]:
    """Resolve SDK compatibility and dependency ordering.

    Parameters
    ----------
    candidates:
        Flat list of :class:`Candidate` objects from discovery.
    sdk_version:
        The current SDK version (e.g. ``"1.0.0"``).

    Returns
    -------
    (loadable, skipped)
        *loadable*: candidates in topological load order.
        *skipped*: ``(candidate, reason)`` for each excluded candidate.
    """
    result = TopologyResolver(candidates, sdk_version).resolve()
    return result.sorted_candidates, result.skipped
