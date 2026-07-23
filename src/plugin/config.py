"""Plugin configuration schema loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from importlib import import_module
from typing import Any


class PluginConfigError(Exception):
    """Raised when a plugin config schema cannot be loaded or validated."""


@dataclass(frozen=True)
class PluginConfigSchema:
    """A loaded and validated plugin config schema class reference."""

    cls: type
    module_name: str
    class_name: str


def load_schema(schema_ref: str) -> PluginConfigSchema | None:
    """Parse a ``module:Class`` schema reference, import and validate it.

    Returns ``None`` when *schema_ref* is empty.  Raises
    :class:`PluginConfigError` on import or validation failure.
    """
    if not schema_ref:
        return None

    if ":" not in schema_ref:
        raise PluginConfigError(
            f"Schema reference must be 'module:Class', got '{schema_ref}'"
        )

    module_name, class_name = schema_ref.split(":", 1)

    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise PluginConfigError(
            f"Cannot import schema module '{module_name}': {exc}"
        ) from exc

    cls = getattr(module, class_name, None)
    if cls is None:
        raise PluginConfigError(
            f"Schema class '{class_name}' not found in module '{module_name}'"
        )

    if not is_dataclass(cls):
        raise PluginConfigError(
            f"'{schema_ref}' must be a dataclass, got {type(cls).__name__}"
        )

    return PluginConfigSchema(cls=cls, module_name=module_name, class_name=class_name)


def build_plugin_config(
    schema: PluginConfigSchema,
    raw: dict[str, Any],
) -> Any:
    """Instantiate *schema.cls* with *raw* values.

    Only keys that match dataclass fields are passed through; dataclass
    defaults fill in missing keys.  Unknown keys are silently ignored.
    """
    valid_keys = {f.name for f in fields(schema.cls)}
    filtered = {k: v for k, v in raw.items() if k in valid_keys}
    return schema.cls(**filtered)
