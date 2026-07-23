"""Tests for plugin config schema loading and validation (PLUG-501)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import pytest

from src.plugin.config import (
    PluginConfigError,
    PluginConfigSchema,
    build_plugin_config,
    load_schema,
)

# ---------------------------------------------------------------------------
# Test schemas
# ---------------------------------------------------------------------------


class NotADataclass:
    pass


@dataclass(frozen=True)
class HelloConfig:
    greeting: str = "Hello"
    max_length: int = 100


@dataclass(frozen=True)
class NestedConfig:
    host: str = "localhost"
    port: int = 8080


# ---------------------------------------------------------------------------
# load_schema
# ---------------------------------------------------------------------------


class TestLoadSchema:
    def test_parses_module_class_ref(self):
        """load_schema parses 'module:Class' correctly."""
        schema = load_schema("src.plugin.config:PluginConfigSchema")
        assert schema is not None
        assert schema.cls is PluginConfigSchema
        assert schema.module_name == "src.plugin.config"
        assert schema.class_name == "PluginConfigSchema"

    def test_returns_none_for_empty_string(self):
        """load_schema returns None for empty string."""
        assert load_schema("") is None

    def test_raises_on_missing_colon(self):
        """load_schema raises PluginConfigError when no colon in ref."""
        with pytest.raises(PluginConfigError, match="must be 'module:Class'"):
            load_schema("no_colon_here")

    def test_raises_on_nonexistent_module(self):
        """load_schema raises PluginConfigError for non-existent module."""
        with pytest.raises(PluginConfigError, match="Cannot import"):
            load_schema("nonexistent.module:SomeClass")

    def test_raises_on_nonexistent_class(self):
        """load_schema raises PluginConfigError when class not in module."""
        with pytest.raises(PluginConfigError, match="not found in module"):
            load_schema("src.plugin.config:NonExistentClass")

    def test_raises_on_non_dataclass(self):
        """load_schema raises PluginConfigError when class is not a dataclass."""
        with pytest.raises(PluginConfigError, match="must be a dataclass"):
            load_schema("tests.test_plugin_config:NotADataclass")


# ---------------------------------------------------------------------------
# build_plugin_config
# ---------------------------------------------------------------------------


class TestBuildPluginConfig:
    def test_creates_instance_from_raw_dict(self):
        """build_plugin_config creates a dataclass instance from raw dict."""
        schema = PluginConfigSchema(cls=HelloConfig, module_name="m", class_name="C")
        raw = {"greeting": "你好", "max_length": 200}
        result = build_plugin_config(schema, raw)
        assert isinstance(result, HelloConfig)
        assert result.greeting == "你好"
        assert result.max_length == 200

    def test_uses_defaults_for_missing_keys(self):
        """build_plugin_config uses dataclass defaults for missing keys."""
        schema = PluginConfigSchema(cls=HelloConfig, module_name="m", class_name="C")
        result = build_plugin_config(schema, {})
        assert result.greeting == "Hello"
        assert result.max_length == 100

    def test_ignores_unknown_keys(self):
        """build_plugin_config silently ignores keys not in the dataclass."""
        schema = PluginConfigSchema(cls=HelloConfig, module_name="m", class_name="C")
        raw = {"greeting": "Hi", "unknown_field": "should be ignored"}
        result = build_plugin_config(schema, raw)
        assert result.greeting == "Hi"
        assert not hasattr(result, "unknown_field")

    def test_frozen_dataclass_cannot_be_mutated(self):
        """Frozen dataclass raises FrozenInstanceError on mutation."""
        schema = PluginConfigSchema(cls=HelloConfig, module_name="m", class_name="C")
        result = build_plugin_config(schema, {"greeting": "Hi"})
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.greeting = "bye"

    def test_partial_override_uses_defaults(self):
        """Only specified keys are overridden; rest use defaults."""
        schema = PluginConfigSchema(cls=HelloConfig, module_name="m", class_name="C")
        raw = {"max_length": 50}
        result = build_plugin_config(schema, raw)
        assert result.greeting == "Hello"
        assert result.max_length == 50
