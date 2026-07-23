"""PLUG-601: SecurityValidator tests — file limits, path safety, forbidden imports,
digest stability, manifest snapshot."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.core.control_plane._security import SecurityReport, SecurityValidator

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_plugin(
    root: Path,
    name: str = "demo_plugin",
    version: str = "1.0.0",
    entrypoint: str | None = None,
    *,
    python_content: str = "VALUE = 42\n",
    extra_files: dict[str, str] | None = None,
    permissions: str | None = None,
) -> Path:
    """Create a minimal plugin candidate directory."""
    root.mkdir(parents=True, exist_ok=True)
    entrypoint = entrypoint or f"plugins.{name}.plugin:Plugin"
    manifest = f"""\
[plugin]
id = "{name}"
name = "{name}"
version = "{version}"
entrypoint = "{entrypoint}"
sdk = ">=1.0,<2.0"
"""
    if permissions:
        manifest += f"\n{permissions}"
    (root / "plugin.toml").write_text(manifest, encoding="utf-8")
    (root / "plugin.py").write_text(python_content, encoding="utf-8")
    if extra_files:
        for fname, content in extra_files.items():
            (root / fname).write_text(content, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# SecurityReport
# ---------------------------------------------------------------------------


class TestSecurityReport:
    def test_report_is_frozen(self):
        report = SecurityReport(
            valid=True,
            name="test",
            version="1.0",
            entrypoint="main.py",
            permissions={},
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.valid = False  # type: ignore[misc]

    def test_report_fields(self):
        report = SecurityReport(
            valid=True,
            name="hello",
            version="2.0",
            entrypoint="main.py",
            permissions={"qq": ["message.send"]},
            violations=[],
            candidate="/tmp/cand",
            sha256="abc123",
            manifest_snapshot={"name": "hello"},
            file_count=3,
            total_size_bytes=1024,
            symlinks_detected=False,
            warnings=["test warning"],
        )
        assert report.name == "hello"
        assert report.sha256 == "abc123"
        assert report.file_count == 3
        assert report.warnings == ["test warning"]


# ---------------------------------------------------------------------------
# SecurityValidator
# ---------------------------------------------------------------------------


class TestSecurityValidator:
    def test_valid_plugin_passes(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root)
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert report.valid is True
        assert report.name == "demo_plugin"
        assert report.version == "1.0.0"
        assert report.sha256

    def test_missing_manifest_raises(self, tmp_path):
        root = tmp_path / "candidate"
        root.mkdir()
        (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
        validator = SecurityValidator(tmp_path / "plugins")
        with pytest.raises(FileNotFoundError):
            validator.validate(root)

    def test_forbidden_import_detected(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root, python_content="import subprocess\n")
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert report.valid is False
        assert any("subprocess" in v for v in report.violations)

    def test_forbidden_text_detected(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root, python_content="path = '.env'\n")
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert report.valid is False
        assert any(".env" in v for v in report.violations)

    def test_file_size_limit_enforced(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root)
        # Write a file that exceeds 1MB
        big_file = root / "big_file.py"
        big_file.write_bytes(b"x" * 1_100_000)
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert report.valid is False
        assert any("too large" in v for v in report.violations)

    def test_total_size_limit_enforced(self, tmp_path):
        root = tmp_path / "candidate"
        # Create many files totalling >10MB
        _write_plugin(root)
        for i in range(12):
            (root / f"data_{i}.bin").write_bytes(b"\x00" * 1_000_000)
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert report.valid is False
        assert any("total size" in v for v in report.violations)

    def test_path_traversal_rejected(self, tmp_path):
        root = tmp_path / "candidate"
        root.mkdir(parents=True)
        # Create a symlink outside the candidate
        # Simulate by creating a relative path that breaks containment
        _write_plugin(root, entrypoint="../../evil:Plugin")
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert report.valid is False
        assert any("entrypoint" in v.lower() for v in report.violations)

    def test_manifest_snapshot_included(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root)
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert report.manifest_snapshot is not None
        assert report.manifest_snapshot["id"] == "demo_plugin"
        assert report.manifest_snapshot["version"] == "1.0.0"

    def test_sha256_digest_is_stable(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root)
        validator = SecurityValidator(tmp_path / "plugins")
        report1 = validator.validate(root)
        report2 = validator.validate(root)
        assert report1.sha256 == report2.sha256

    def test_sha256_changes_with_content(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root, python_content="VALUE = 1\n")
        validator = SecurityValidator(tmp_path / "plugins")
        report1 = validator.validate(root)

        # Change content
        (root / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
        report2 = validator.validate(root)
        assert report1.sha256 != report2.sha256

    def test_invalid_plugin_name_rejected(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root, name="INVALID_NAME")
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert report.valid is False
        assert any("invalid plugin.id" in v.lower() for v in report.violations)

    def test_syntax_error_in_source_reported(self, tmp_path):
        root = tmp_path / "candidate"
        _write_plugin(root, python_content="def broken(:\n")
        validator = SecurityValidator(tmp_path / "plugins")
        report = validator.validate(root)
        assert any("syntax error" in v for v in report.violations)
