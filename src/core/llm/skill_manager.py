"""Skill discovery and hot-reload for progressive tool loading."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef


@dataclass
class SkillsConfig:
    """Configuration for the skill subsystem."""

    enabled: bool = True
    skills_dir: str = "config/skills"
    auto_reload: bool = True


@dataclass
class SkillDef:
    """Parsed skill from a ``skill.md`` file."""

    name: str
    description: str
    category: str = "query"
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    parameters: dict[str, Any] = field(default_factory=dict)
    full_prompt: str = ""       # full markdown body for progressive loading
    file_path: str = ""
    _mtime: float = 0

    def to_tool_def(self, executor) -> ToolDef:
        """Convert to a :class:`ToolDef` for registration in the catalog."""
        params_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for pname, pinfo in self.parameters.items():
            params_schema["properties"][pname] = {
                "type": pinfo.get("type", "string"),
                "description": pinfo.get("description", ""),
            }
            if pinfo.get("required"):
                params_schema["required"].append(pname)
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=params_schema,
            risk_level=self.risk_level,
            category=self.category,
            executor=executor,
        )


class SkillManager:
    """Discovers skill definitions from ``config/skills/`` and registers them
    as :class:`ToolDef` entries in a :class:`ToolCatalog`.

    Supports progressive loading: only name + description are sent to the LLM;
    the full prompt body is loaded when the tool is actually invoked.
    """

    def __init__(
        self,
        catalog: ToolCatalog,
        config: SkillsConfig | None = None,
    ) -> None:
        self.catalog = catalog
        self.config = config or SkillsConfig()
        self._skills: dict[str, SkillDef] = {}
        self._skills_dir = Path(self.config.skills_dir)

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------

    def discover(self, bot) -> int:
        """Scan ``config/skills/`` and register discovered skills.

        Each subdirectory with a ``skill.md`` becomes a skill.
        Returns the number of skills loaded.
        """
        if not self.config.enabled:
            return 0

        if not self._skills_dir.is_dir():
            logger.debug(f"Skills directory not found: {self._skills_dir}")
            return 0

        count = 0
        for entry in sorted(self._skills_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            skill_md = entry / "skill.md"
            if not skill_md.is_file():
                continue
            try:
                skill = self._parse_skill(skill_md)
                if skill:
                    self._register_skill(skill, bot)
                    count += 1
            except Exception:
                logger.opt(exception=True).warning(f"Failed to load skill from {skill_md}")

        if count:
            logger.info(f"Loaded {count} skill(s) from {self._skills_dir}")
        return count

    def hot_reload(self, name: str, bot) -> bool:
        """Reload a single skill if its file has changed. Returns True if reloaded."""
        skill = self._skills.get(name)
        if not skill or not skill.file_path:
            return False
        path = Path(skill.file_path)
        if not path.is_file():
            return False
        mtime = path.stat().st_mtime
        if mtime == skill._mtime:
            return False
        try:
            new_skill = self._parse_skill(path)
            if new_skill:
                self.catalog.unregister(name)
                self._register_skill(new_skill, bot)
                logger.info(f"Skill '{name}' hot-reloaded")
                return True
        except Exception:
            logger.opt(exception=True).warning(f"Failed to hot-reload skill '{name}'")
        return False

    # ------------------------------------------------------------------
    # skill lookup (for progressive loading)
    # ------------------------------------------------------------------

    def get_full_prompt(self, name: str) -> str | None:
        """Return the full markdown body for a skill, or None."""
        skill = self._skills.get(name)
        return skill.full_prompt if skill else None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _parse_skill(self, path: Path) -> SkillDef | None:
        """Parse a single ``skill.md`` file.

        Expected format: YAML front matter (--- … ---) followed by markdown body.
        Supports either TOML or YAML-style front matter.
        """
        text = path.read_text(encoding="utf-8")
        mtime = path.stat().st_mtime

        # Extract front matter between --- delimiters (TOML format)
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
        if fm_match:
            front_matter_str = fm_match.group(1)
            body = fm_match.group(2).strip()
            try:
                meta = tomllib.loads(front_matter_str)
            except Exception:
                # Try as simple key: value YAML
                meta = _parse_simple_yaml(front_matter_str)
        else:
            # No front matter: use filename as name
            meta = {"name": path.parent.name}
            body = text.strip()

        name = meta.get("name", path.parent.name)
        return SkillDef(
            name=name,
            description=meta.get("description", f"Skill: {name}"),
            category=meta.get("category", "query"),
            risk_level=RiskLevel(meta.get("risk_level", "read_only")),
            parameters=meta.get("parameters", {}),
            full_prompt=body,
            file_path=str(path),
            _mtime=mtime,
        )

    def _register_skill(self, skill: SkillDef, bot) -> None:
        """Create a ToolDef and register it in the catalog."""

        async def _exec_skill(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
            """Execute a skill by returning its full prompt for the LLM."""
            return {"skill_prompt": skill.full_prompt, "skill_name": skill.name}

        tool_def = skill.to_tool_def(_exec_skill)
        self.catalog.register(tool_def)
        self._skills[skill.name] = skill


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML-like parser for simple key: value pairs."""
    result: dict[str, Any] = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\w[\w_-]*)\s*:\s*(.+)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
            result[k] = v
    return result
