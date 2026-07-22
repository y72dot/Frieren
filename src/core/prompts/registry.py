"""Load, validate, compose, and render versioned prompt profiles."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    version: str
    profile: str
    parts: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class _Profile:
    parts: tuple[str, ...]
    extends: str = ""
    append: tuple[str, ...] = ()


class PromptRegistry:
    def __init__(
        self,
        *,
        version: str,
        profiles: dict[str, _Profile],
        parts: dict[str, str],
    ) -> None:
        self.version = version
        self._profiles = profiles
        self._parts = parts
        self.validate()

    @classmethod
    def load(cls, prompts_dir: str | Path) -> PromptRegistry:
        root = Path(prompts_dir)
        manifest_path = root / "manifest.toml"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Prompt manifest not found: {manifest_path}")
        with manifest_path.open("rb") as fh:
            raw = tomllib.load(fh)
        version = str(raw.get("version", "")).strip()
        if not version:
            raise ValueError("Prompt manifest requires a non-empty version")

        profiles: dict[str, _Profile] = {}
        referenced_parts: set[str] = set()
        for name, data in raw.get("profiles", {}).items():
            profile = _Profile(
                parts=tuple(str(x) for x in data.get("parts", [])),
                extends=str(data.get("extends", "")),
                append=tuple(str(x) for x in data.get("append", [])),
            )
            profiles[str(name)] = profile
            referenced_parts.update(profile.parts)
            referenced_parts.update(profile.append)

        parts: dict[str, str] = {}
        for name in referenced_parts:
            path = root / f"{name}.md"
            if not path.is_file():
                raise FileNotFoundError(f"Prompt part not found: {path}")
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                raise ValueError(f"Prompt part is empty: {path}")
            parts[name] = content
        return cls(version=version, profiles=profiles, parts=parts)

    @classmethod
    def from_legacy(cls, system_prompt: str) -> PromptRegistry:
        return cls(
            version="legacy",
            profiles={"default": _Profile(parts=("legacy",))},
            parts={"legacy": system_prompt.strip()},
        )

    def validate(self) -> None:
        if not self._profiles:
            raise ValueError("Prompt registry has no profiles")
        for name in self._profiles:
            self._resolve_parts(name, stack=())

    def render(
        self,
        profile: str = "default",
        context: dict[str, Any] | None = None,
    ) -> RenderedPrompt:
        part_names = self._resolve_parts(profile, stack=())
        values = {k: str(v) for k, v in (context or {}).items()}
        rendered_parts = [Template(self._parts[name]).safe_substitute(values) for name in part_names]
        text = "\n\n".join(rendered_parts).strip()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RenderedPrompt(
            text=text,
            version=self.version,
            profile=profile,
            parts=part_names,
            sha256=digest,
        )

    def _resolve_parts(self, profile: str, stack: tuple[str, ...]) -> tuple[str, ...]:
        if profile not in self._profiles:
            raise KeyError(f"Unknown prompt profile: {profile}")
        if profile in stack:
            chain = " -> ".join((*stack, profile))
            raise ValueError(f"Prompt profile inheritance cycle: {chain}")
        spec = self._profiles[profile]
        resolved: tuple[str, ...] = ()
        if spec.extends:
            resolved = self._resolve_parts(spec.extends, (*stack, profile))
        resolved = (*resolved, *spec.parts, *spec.append)
        missing = [name for name in resolved if name not in self._parts]
        if missing:
            raise ValueError(f"Prompt profile '{profile}' references missing parts: {missing}")
        if not resolved:
            raise ValueError(f"Prompt profile '{profile}' has no parts")
        # Preserve declaration order while avoiding accidental duplicate modules.
        return tuple(dict.fromkeys(resolved))
