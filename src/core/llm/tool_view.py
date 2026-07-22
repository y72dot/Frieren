"""Per-request immutable view over the global LLM tool catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.llm.tool_catalog import ToolDef


@dataclass(frozen=True)
class ToolView:
    """Tools visible to one model request, preserving catalog order."""

    tools: tuple[ToolDef, ...]
    active_packs: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self.tools]

    def __len__(self) -> int:
        return len(self.tools)
