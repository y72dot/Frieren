"""P1 PLUG-101: Manifest model parsing and validation tests."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.plugin.manifest import (
    ManifestConfig,
    ManifestError,
    ManifestParseError,
    ManifestPermissions,
    ManifestValidationError,
    parse_manifest,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, content: str, name: str = "plugin.toml") -> Path:
    """Write a manifest file and return its path."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# valid cases
# ---------------------------------------------------------------------------


class TestManifestParsing:
    def test_valid_minimal_manifest(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "hello"
            version = "1.0.0"
            entrypoint = "hello.plugin:plugin"
            sdk = ">=1.0,<2.0"
            """,
        )
        m = parse_manifest(path)
        assert m.id == "hello"
        assert m.name == "hello"  # defaults to id
        assert m.version == "1.0.0"
        assert m.entrypoint == "hello.plugin:plugin"
        assert m.sdk == ">=1.0,<2.0"
        assert m.description == ""
        assert m.dependencies == []
        assert m.permissions == ManifestPermissions()
        assert m.config is None

    def test_valid_full_manifest(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "my_plugin"
            name = "My Plugin"
            version = "2.0.0-beta+1"
            entrypoint = "my_plugin.core:create_plugin"
            sdk = ">=1.0,<3.0"
            description = "A full-featured plugin"

            [dependencies]
            plugins = ["other_plugin", "helper"]

            [permissions]
            qq = ["message.send", "message.react"]
            storage = ["plugin.read"]
            scheduler = true
            network = ["http"]

            [config]
            schema = "my_plugin.config:MyConfig"
            """,
        )
        m = parse_manifest(path)
        assert m.id == "my_plugin"
        assert m.name == "My Plugin"
        assert m.version == "2.0.0-beta+1"
        assert m.entrypoint == "my_plugin.core:create_plugin"
        assert m.sdk == ">=1.0,<3.0"
        assert m.description == "A full-featured plugin"
        assert m.dependencies == ["other_plugin", "helper"]
        assert m.permissions.qq == ["message.send", "message.react"]
        assert m.permissions.storage == ["plugin.read"]
        assert m.permissions.scheduler is True
        assert m.permissions.network == ["http"]
        assert m.config == ManifestConfig(schema="my_plugin.config:MyConfig")

    def test_name_defaults_to_id(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "test:plugin"
            sdk = "*"
            """,
        )
        m = parse_manifest(path)
        assert m.name == "test"

    def test_name_explicit_overrides_default(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            name = "Test Plugin"
            version = "1.0.0"
            entrypoint = "test:plugin"
            sdk = "*"
            """,
        )
        m = parse_manifest(path)
        assert m.name == "Test Plugin"

    def test_empty_permissions_section(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "min"
            version = "1.0.0"
            entrypoint = "min:plugin"
            sdk = "*"

            [permissions]
            """,
        )
        m = parse_manifest(path)
        assert m.permissions == ManifestPermissions()

    def test_manifest_is_frozen(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "x"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        m = parse_manifest(path)
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.id = "other"  # type: ignore[misc]

    def test_semver_with_prerelease(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "alpha"
            version = "1.0.0-alpha.1"
            entrypoint = "a:b"
            sdk = "*"
            """,
        )
        m = parse_manifest(path)
        assert m.version == "1.0.0-alpha.1"

    def test_semver_with_build_metadata(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "build"
            version = "1.0.0+20260101"
            entrypoint = "b:c"
            sdk = "*"
            """,
        )
        m = parse_manifest(path)
        assert m.version == "1.0.0+20260101"


# ---------------------------------------------------------------------------
# validation – invalid fields
# ---------------------------------------------------------------------------


class TestManifestValidation:
    def test_missing_id(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("id" in e.lower() for e in exc.value.errors)

    def test_missing_version(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("version" in e.lower() for e in exc.value.errors)

    def test_missing_entrypoint(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("entrypoint" in e.lower() for e in exc.value.errors)

    def test_missing_sdk(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "x:y"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("sdk" in e.lower() for e in exc.value.errors)

    def test_invalid_id_uppercase(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "Hello"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("Hello" in e for e in exc.value.errors)

    def test_invalid_id_hyphen(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "my-plugin"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("my-plugin" in e for e in exc.value.errors)

    def test_invalid_id_leading_underscore(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "_private"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("_private" in e for e in exc.value.errors)

    def test_invalid_id_leading_digit(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "123abc"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("123abc" in e for e in exc.value.errors)

    def test_invalid_version_not_semver(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("1.0" in e for e in exc.value.errors)

    def test_invalid_entrypoint_no_colon(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "just_a_module"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("entrypoint" in e.lower() for e in exc.value.errors)

    def test_invalid_entrypoint_path_escape(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "../../evil:func"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any(".." in e for e in exc.value.errors)

    def test_unknown_top_level_key(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"

            [extra]
            foo = "bar"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("extra" in e for e in exc.value.errors)

    def test_unknown_plugin_key(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            priority = 100
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("priority" in e for e in exc.value.errors)

    def test_unknown_permissions_key(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"

            [permissions]
            qq = ["message.send"]
            filesystem = true
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("filesystem" in e for e in exc.value.errors)

    def test_invalid_qq_permission(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"

            [permissions]
            qq = ["message.send", "admin.delete"]
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("admin.delete" in e for e in exc.value.errors)

    def test_invalid_storage_permission(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"

            [permissions]
            storage = ["global.admin"]
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("global.admin" in e for e in exc.value.errors)

    def test_invalid_network_permission(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"

            [permissions]
            network = ["tcp"]
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("tcp" in e for e in exc.value.errors)

    def test_scheduler_not_boolean(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "test"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"

            [permissions]
            scheduler = "yes"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("scheduler" in e.lower() for e in exc.value.errors)

    def test_multiple_validation_errors(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "INVALID"
            version = "bad"
            entrypoint = "no_colon"

            [permissions]
            qq = ["bogus.perm"]
            scheduler = "nope"

            [unknown_section]
            x = 1
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        # Should have collected multiple errors, not just the first one.
        assert len(exc.value.errors) >= 3

    def test_plugin_section_must_be_table(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            plugin = "not a table"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert any("table" in e.lower() for e in exc.value.errors)


# ---------------------------------------------------------------------------
# I/O errors
# ---------------------------------------------------------------------------


class TestManifestErrors:
    def test_file_not_found(self, tmp_path):
        path = tmp_path / "does_not_exist.toml"
        with pytest.raises(ManifestParseError) as exc:
            parse_manifest(path)
        assert "not found" in str(exc.value).lower()

    def test_invalid_toml_syntax(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin
            id = "test"
            """,
        )
        with pytest.raises(ManifestParseError) as exc:
            parse_manifest(path)
        assert "toml" in str(exc.value).lower() or "parse" in str(exc.value).lower()

    def test_manifest_error_is_base(self):
        assert issubclass(ManifestParseError, ManifestError)
        assert issubclass(ManifestValidationError, ManifestError)

    def test_validation_error_carries_errors_list(self, tmp_path):
        path = _write_manifest(
            tmp_path,
            """\
            [plugin]
            id = "BAD"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        with pytest.raises(ManifestValidationError) as exc:
            parse_manifest(path)
        assert isinstance(exc.value.errors, list)
        assert len(exc.value.errors) >= 1

    def test_garbage_binary_file(self, tmp_path):
        path = tmp_path / "plugin.toml"
        path.write_bytes(b"\x00\x01\x02\xff\xfe")
        with pytest.raises(ManifestParseError):
            parse_manifest(path)
