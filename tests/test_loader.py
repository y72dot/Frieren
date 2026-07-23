"""P1 PLUG-102: Loader discovery tests – PackageLoader, LegacyLoader, discover_candidates."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.plugin.loader import (
    LoaderType,
    PackageLoader,
    discover_candidates,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_manifest(path: Path, content: str) -> Path:
    """Write a plugin.toml file in the given directory."""
    p = path / "plugin.toml"
    p.write_text(content, encoding="utf-8")
    return p


def _create_package_plugin(tmp_path: Path, name: str, manifest_content: str) -> Path:
    """Create a package plugin directory with plugin.toml. Returns the dir path."""
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True)
    _write_manifest(plugin_dir, manifest_content)
    return plugin_dir


def _create_legacy_plugin(tmp_path: Path, name: str, content: str = "") -> Path:
    """Create a .py file in tmp_path. Returns the py file path."""
    py_file = tmp_path / f"{name}.py"
    py_file.write_text(content or "# legacy plugin", encoding="utf-8")
    return py_file


def _make_minimal_manifest(id: str = "test") -> str:
    return f"""\
[plugin]
id = "{id}"
version = "1.0.0"
entrypoint = "{id}.core:create_plugin"
sdk = ">=1.0,<2.0"
"""


# ---------------------------------------------------------------------------
# PackageLoader
# ---------------------------------------------------------------------------


class TestPackageLoader:
    def test_finds_plugin_dir_with_toml(self, tmp_path):
        _create_package_plugin(tmp_path, "hello", _make_minimal_manifest("hello"))
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.plugin_id == "hello"
        assert c.loader_type == LoaderType.PACKAGE
        assert c.manifest.id == "hello"
        assert c.manifest.version == "1.0.0"

    def test_ignores_dir_without_toml(self, tmp_path):
        (tmp_path / "empty_dir").mkdir()
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        assert len(candidates) == 0

    def test_non_recursive(self, tmp_path):
        nested = tmp_path / "outer" / "inner"
        nested.mkdir(parents=True)
        _write_manifest(nested, _make_minimal_manifest("inner"))
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        # Only scans immediate subdirs, so outer/inner not found.
        plugin_ids = [c.plugin_id for c in candidates]
        assert "inner" not in plugin_ids

    def test_invalid_manifest_skipped(self, tmp_path):
        _create_package_plugin(
            tmp_path,
            "bad",
            """\
            [plugin]
            id = "BAD"
            version = "1.0.0"
            entrypoint = "x:y"
            sdk = ">=1.0"
            """,
        )
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        # Bad ID is a validation error → candidate is skipped.
        assert len(candidates) == 0

    def test_entrypoint_path_escape_rejected(self, tmp_path):
        plugin_dir = _create_package_plugin(
            tmp_path,
            "escape_test",
            """\
            [plugin]
            id = "escape_test"
            version = "1.0.0"
            entrypoint = "../../evil:func"
            sdk = ">=1.0"
            """,
        )
        # Also create the actual entrypoint file for the path check.
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        # Path escape is caught by _check_entrypoint_path → skipped.
        assert len(candidates) == 0

    def test_two_package_plugins(self, tmp_path):
        _create_package_plugin(tmp_path, "alpha", _make_minimal_manifest("alpha"))
        _create_package_plugin(tmp_path, "beta", _make_minimal_manifest("beta"))
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        assert len(candidates) == 2
        ids = [c.plugin_id for c in candidates]
        assert "alpha" in ids
        assert "beta" in ids

    def test_skips_underscore_prefix_dir(self, tmp_path):
        _create_package_plugin(tmp_path, "_internal", _make_minimal_manifest("internal"))
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        assert len(candidates) == 0

    def test_skips_dot_prefix_dir(self, tmp_path):
        _create_package_plugin(tmp_path, ".hidden", _make_minimal_manifest("hidden"))
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        assert len(candidates) == 0

    def test_candidate_path_is_absolute(self, tmp_path):
        _create_package_plugin(tmp_path, "abs_test", _make_minimal_manifest("abs_test"))
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        assert len(candidates) == 1
        assert candidates[0].path.is_absolute()

    def test_candidate_source_module_is_empty(self, tmp_path):
        _create_package_plugin(tmp_path, "pkg", _make_minimal_manifest("pkg"))
        loader = PackageLoader()
        candidates = loader.scan(tmp_path)
        assert candidates[0].source_module == ""

    def test_missing_dir_returns_empty(self, tmp_path):
        loader = PackageLoader()
        candidates = loader.scan(tmp_path / "nonexistent")
        assert candidates == []


# ---------------------------------------------------------------------------
# discover_candidates (integration)
# ---------------------------------------------------------------------------


class TestDiscoverCandidates:
    def test_finds_package_plugins(self, tmp_path):
        _create_package_plugin(tmp_path, "pkg_plugin", _make_minimal_manifest("pkg_plugin"))
        candidates = discover_candidates([str(tmp_path)])
        ids = [c.plugin_id for c in candidates]
        assert ids == ["pkg_plugin"]

    def test_result_sorted_by_plugin_id(self, tmp_path):
        _create_package_plugin(tmp_path, "zzz", _make_minimal_manifest("zzz"))
        _create_package_plugin(tmp_path, "aaa", _make_minimal_manifest("aaa"))
        _create_package_plugin(tmp_path, "mmm", _make_minimal_manifest("mmm"))

        candidates = discover_candidates([str(tmp_path)])
        ids = [c.plugin_id for c in candidates]
        assert ids == sorted(ids)

    def test_empty_dir_returns_empty(self, tmp_path):
        candidates = discover_candidates([str(tmp_path)])
        assert candidates == []

    def test_missing_dir_returns_empty(self, tmp_path):
        candidates = discover_candidates([str(tmp_path / "nope")])
        assert candidates == []

    def test_deduplication_same_id_in_different_dirs(self, tmp_path):
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        _create_package_plugin(dir_a, "dupe", _make_minimal_manifest("dupe"))
        _create_package_plugin(dir_b, "dupe", _make_minimal_manifest("dupe"))

        candidates = discover_candidates([str(dir_a), str(dir_b)])
        ids = [c.plugin_id for c in candidates]
        assert ids.count("dupe") == 1

    def test_candidate_is_frozen(self, tmp_path):
        _create_package_plugin(tmp_path, "frozen_test", _make_minimal_manifest("frozen_test"))
        candidates = discover_candidates([str(tmp_path)])
        c = candidates[0]
        with pytest.raises(Exception):
            c.plugin_id = "changed"  # type: ignore[misc]
