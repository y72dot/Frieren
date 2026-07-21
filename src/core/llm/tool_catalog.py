"""Structured tool registry replacing the raw TOOL_DEFS list."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.core.llm.sandbox import RiskLevel


@dataclass
class ToolDef:
    """Structured definition of an LLM-callable tool."""

    name: str
    description: str
    parameters: dict            # OpenAI JSON Schema for the function
    risk_level: RiskLevel
    category: str               # query / management / interaction / perception / cognition
    executor: Callable          # async callable(args, group_id, user_id, bot) -> dict
    requires_admin: bool = False
    cache_ttl: float = 0        # result cache TTL in seconds, 0 = no cache

    def to_openai_schema(self) -> dict[str, Any]:
        """Return the OpenAI function-calling schema dict for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolCatalog:
    """Registry of :class:`ToolDef` objects with lookup and filtering."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    # -- mutation ---------------------------------------------------------

    def register(self, tool: ToolDef) -> None:
        """Add or replace a tool definition."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Remove a tool by name (no-op if not found)."""
        self._tools.pop(name, None)

    # -- queries ----------------------------------------------------------

    def get(self, name: str) -> ToolDef | None:
        """Look up a single tool definition by name."""
        return self._tools.get(name)

    def get_defs(self, user_is_admin: bool = False) -> list[dict[str, Any]]:
        """Return OpenAI-format tool schemas visible to the caller.

        Non-admin callers do not see tools marked ``requires_admin=True``.
        """
        result: list[dict[str, Any]] = []
        for t in self._tools.values():
            if t.requires_admin and not user_is_admin:
                continue
            result.append(t.to_openai_schema())
        return result

    def get_all_defs(self) -> list[dict[str, Any]]:
        """Return every registered tool schema (backward-compatible with TOOL_DEFS)."""
        return [t.to_openai_schema() for t in self._tools.values()]

    @property
    def count(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())
