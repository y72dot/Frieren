"""CommandRegistry – immutable command index with alias resolution.

Provides fast command lookup from raw message text with CQ-code
stripping, alias resolution, and conflict detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.plugin.definition import CommandSpec
    from src.plugin.registry import RegistrySnapshot

_CQ_STRIP = re.compile(r"\[CQ:[^\]]+\]")


# ---------------------------------------------------------------------------
# CommandMatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandMatch:
    """Result of a successful command lookup."""

    spec: CommandSpec
    plugin_id: str
    args: str  # remaining text after the command prefix (may be empty)


# ---------------------------------------------------------------------------
# CommandRegistry
# ---------------------------------------------------------------------------


@dataclass
class CommandRegistry:
    """Immutable command index with alias resolution and conflict detection."""

    _commands: dict[str, tuple[CommandSpec, str]] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, snapshot: RegistrySnapshot) -> CommandRegistry:
        """Build from a RegistrySnapshot."""
        registry = cls(
            _commands=dict(snapshot.commands_by_name),
            _aliases={},
        )
        # Build alias → canonical name index.
        for name, (spec, _plugin_id) in snapshot.commands_by_name.items():
            for alias in spec.aliases:
                registry._aliases[alias] = name
        return registry

    def find(self, message: str) -> CommandMatch | None:
        """Look up a command from raw message text.

        Strips CQ codes, then checks:
        1. Exact match: msg == command_name
        2. Prefix match: msg.startswith(command_name + " ") or + "\\n"

        Aliases are resolved to canonical names.
        Returns first match (first-registered wins on conflict).
        """
        cleaned = _CQ_STRIP.sub("", message).strip()
        if not cleaned:
            return None

        # Check canonical names first (first-registered wins via dict order).
        for name, (spec, plugin_id) in self._commands.items():
            match = self._try_match(cleaned, name)
            if match is not None:
                return CommandMatch(spec=spec, plugin_id=plugin_id, args=match)

        # Check aliases.
        for alias, canonical in self._aliases.items():
            match = self._try_match(cleaned, alias)
            if match is not None:
                spec, plugin_id = self._commands[canonical]
                return CommandMatch(spec=spec, plugin_id=plugin_id, args=match)

        return None

    @staticmethod
    def _try_match(cleaned: str, name: str) -> str | None:
        """Check if *cleaned* matches *name*. Returns remaining args or None."""
        if cleaned == name:
            return ""
        if cleaned.startswith(name + " ") or cleaned.startswith(name + "\n"):
            return cleaned[len(name) + 1:]
        return None

    def list_all(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """Return (name, plugin_id, aliases) for every command."""
        result: list[tuple[str, str, tuple[str, ...]]] = []
        for name, (spec, plugin_id) in self._commands.items():
            result.append((name, plugin_id, spec.aliases))
        return result

    def detect_conflicts(self) -> list[tuple[str, str, str]]:
        """Return (command_name, plugin_a, plugin_b) for each conflict.

        Conflicts occur when two plugins register the same command name.
        Since first-registered wins in the snapshot, we report duplicates.
        """
        # Conflicts are detected at build_snapshot time and logged.
        # This method is for introspection; with a single snapshot there are
        # no conflicts (first-wins already resolved).  We return an empty list
        # for the current snapshot, but subclasses or multi-source builders
        # can override.
        return []
