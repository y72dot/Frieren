"""P1 PLUG-104: SDK constraint and dependency topology tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.plugin.loader import Candidate, LoaderType
from src.plugin.manifest import PluginManifest
from src.plugin.topology import (
    SdkConstraint,
    SdkConstraintError,
    TopologyResolver,
    resolve_candidates,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    plugin_id: str,
    sdk: str = ">=1.0,<2.0",
    dependencies: list[str] | None = None,
    loader_type: LoaderType = LoaderType.PACKAGE,
) -> Candidate:
    """Create a minimal Candidate for topology testing."""
    manifest = PluginManifest(
        id=plugin_id,
        version="1.0.0",
        entrypoint=f"{plugin_id}:main",
        sdk=sdk,
        dependencies=dependencies or [],
    )
    return Candidate(
        plugin_id=plugin_id,
        path=Path(f"/fake/{plugin_id}"),
        manifest=manifest,
        loader_type=loader_type,
        source_module="",
    )


# ---------------------------------------------------------------------------
# SdkConstraint.parse
# ---------------------------------------------------------------------------


class TestSdkConstraintParse:
    def test_parse_single_ge(self):
        c = SdkConstraint.parse(">=1.0.0")
        assert not c.wildcard
        assert len(c.constraints) == 1
        op, ver = c.constraints[0]
        assert op == ">="
        assert ver == (1, 0, 0)

    def test_parse_comma_and(self):
        c = SdkConstraint.parse(">=1.0.0, <2.0.0")
        assert len(c.constraints) == 2

    def test_parse_all_operators(self):
        for op in (">=", "<=", ">", "<", "=="):
            c = SdkConstraint.parse(f"{op}3.2.1")
            assert c.constraints[0][0] == op

    def test_parse_wildcard(self):
        c = SdkConstraint.parse("*")
        assert c.wildcard is True

    def test_parse_empty_string_raises(self):
        with pytest.raises(SdkConstraintError):
            SdkConstraint.parse("")

    def test_parse_whitespace_only_raises(self):
        # Whitespace-only still an error because no valid parts remain.
        with pytest.raises(SdkConstraintError):
            SdkConstraint.parse("   ")

    def test_parse_invalid_op(self):
        with pytest.raises(SdkConstraintError) as exc:
            SdkConstraint.parse("!=1.0.0")
        assert any("!=" in e for e in exc.value.errors)

    def test_parse_invalid_format(self):
        with pytest.raises(SdkConstraintError) as exc:
            SdkConstraint.parse("1.0")
        assert any("1.0" in e for e in exc.value.errors)

    def test_parse_mixed_valid_and_invalid(self):
        with pytest.raises(SdkConstraintError) as exc:
            SdkConstraint.parse(">=1.0.0, bad, <2.0.0")
        assert any("bad" in e for e in exc.value.errors)


# ---------------------------------------------------------------------------
# SdkConstraint.check
# ---------------------------------------------------------------------------


class TestSdkConstraintCheck:
    def test_check_satisfied(self):
        c = SdkConstraint.parse(">=1.0.0, <2.0.0")
        assert c.check("1.5.0") is True

    def test_check_too_low(self):
        c = SdkConstraint.parse(">=2.0.0")
        assert c.check("1.0.0") is False

    def test_check_too_high(self):
        c = SdkConstraint.parse("<2.0.0")
        assert c.check("2.0.0") is False

    def test_check_edge_equal_ge(self):
        c = SdkConstraint.parse(">=1.0.0")
        assert c.check("1.0.0") is True

    def test_check_edge_equal_le(self):
        c = SdkConstraint.parse("<=1.0.0")
        assert c.check("1.0.0") is True

    def test_check_edge_less(self):
        c = SdkConstraint.parse("<2.0.0")
        assert c.check("1.999.999") is True

    def test_check_exact(self):
        c = SdkConstraint.parse("==1.5.0")
        assert c.check("1.5.0") is True
        assert c.check("1.5.1") is False

    def test_check_greater(self):
        c = SdkConstraint.parse(">1.0.0")
        assert c.check("1.0.1") is True
        assert c.check("1.0.0") is False
        assert c.check("0.9.9") is False

    def test_check_wildcard_always_passes(self):
        c = SdkConstraint.parse("*")
        assert c.check("0.0.0") is True
        assert c.check("999.999.999") is True

    def test_check_prerelease_version(self):
        c = SdkConstraint.parse(">=1.0.0, <3.0.0")
        # Prerelease parts are ignored by _parse_version_tuple.
        assert c.check("2.0.0-beta") is True

    def test_check_version_with_build_metadata(self):
        c = SdkConstraint.parse(">=1.0.0, <2.0.0")
        assert c.check("1.0.0+20260101") is True


# ---------------------------------------------------------------------------
# TopologyResolver
# ---------------------------------------------------------------------------


class TestTopologyResolver:
    def test_no_dependencies(self):
        candidates = [
            _make_candidate("a"),
            _make_candidate("b"),
            _make_candidate("c"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        assert len(result.sorted_candidates) == 3
        assert len(result.skipped) == 0

    def test_linear_chain(self):
        # c depends on b, b depends on a → order: a, b, c
        candidates = [
            _make_candidate("c", dependencies=["b"]),
            _make_candidate("b", dependencies=["a"]),
            _make_candidate("a"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        ids = [c.plugin_id for c in result.sorted_candidates]
        a_idx = ids.index("a")
        b_idx = ids.index("b")
        c_idx = ids.index("c")
        assert a_idx < b_idx < c_idx

    def test_diamond_dependency(self):
        # d depends on b and c; b and c depend on a → a first
        candidates = [
            _make_candidate("d", dependencies=["b", "c"]),
            _make_candidate("b", dependencies=["a"]),
            _make_candidate("c", dependencies=["a"]),
            _make_candidate("a"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        ids = [c.plugin_id for c in result.sorted_candidates]
        a_idx = ids.index("a")
        d_idx = ids.index("d")
        assert a_idx < d_idx
        assert ids.index("b") > a_idx
        assert ids.index("c") > a_idx

    def test_missing_dependency_skipped(self):
        candidates = [
            _make_candidate("orphan", dependencies=["nonexistent"]),
            _make_candidate("a"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        assert len(result.sorted_candidates) == 1
        assert result.sorted_candidates[0].plugin_id == "a"
        assert len(result.skipped) == 1
        assert "nonexistent" in result.skipped[0][1]

    def test_circular_dependency_skipped(self):
        # a → b → c → a
        candidates = [
            _make_candidate("a", dependencies=["b"]),
            _make_candidate("b", dependencies=["c"]),
            _make_candidate("c", dependencies=["a"]),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        # All 3 are in a cycle → all skipped, zero loadable.
        assert len(result.sorted_candidates) == 0
        assert len(result.skipped) == 3

    def test_sdk_incompatible_skipped(self):
        candidates = [
            _make_candidate("old", sdk=">=2.0.0"),
            _make_candidate("new", sdk=">=1.0.0"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        assert len(result.sorted_candidates) == 1
        assert result.sorted_candidates[0].plugin_id == "new"
        assert len(result.skipped) == 1
        assert "old" in result.skipped[0][0].plugin_id

    def test_sdk_compatible_loaded(self):
        candidates = [
            _make_candidate("plugin", sdk=">=0.5.0, <2.0.0"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        assert len(result.sorted_candidates) == 1
        assert len(result.skipped) == 0

    def test_self_dependency_ignored(self):
        candidates = [
            _make_candidate("self_dep", dependencies=["self_dep"]),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        # Self-dependency is logged as warning but not treated as missing.
        assert len(result.sorted_candidates) == 1
        assert len(result.skipped) == 0

    def test_empty_candidates(self):
        resolver = TopologyResolver([], "1.0.0")
        result = resolver.resolve()
        assert result.sorted_candidates == []
        assert result.skipped == []

    def test_mixed_compatible_and_incompatible(self):
        candidates = [
            _make_candidate("a"),
            _make_candidate("b", sdk=">=99.0.0"),
            _make_candidate("c"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        loaded_ids = [c.plugin_id for c in result.sorted_candidates]
        assert "a" in loaded_ids
        assert "c" in loaded_ids
        assert len(result.skipped) == 1

    def test_complex_graph(self):
        # a
        # b depends on a
        # c depends on a
        # d depends on b, c
        # e depends on d
        candidates = [
            _make_candidate("e", dependencies=["d"]),
            _make_candidate("d", dependencies=["b", "c"]),
            _make_candidate("c", dependencies=["a"]),
            _make_candidate("b", dependencies=["a"]),
            _make_candidate("a"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        ids = [c.plugin_id for c in result.sorted_candidates]
        assert ids.index("a") < ids.index("b")
        assert ids.index("a") < ids.index("c")
        assert ids.index("b") < ids.index("d")
        assert ids.index("c") < ids.index("d")
        assert ids.index("d") < ids.index("e")

    def test_bad_sdk_constraint_skipped(self):
        candidates = [
            _make_candidate("bad", sdk="not-a-constraint"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        assert len(result.skipped) == 1
        assert "bad" in result.skipped[0][0].plugin_id

    def test_missing_dep_but_dep_exists(self):
        # b depends on a, a exists → both load.
        candidates = [
            _make_candidate("b", dependencies=["a"]),
            _make_candidate("a"),
        ]
        resolver = TopologyResolver(candidates, "1.0.0")
        result = resolver.resolve()
        assert len(result.sorted_candidates) == 2
        ids = [c.plugin_id for c in result.sorted_candidates]
        assert ids.index("a") < ids.index("b")


# ---------------------------------------------------------------------------
# resolve_candidates (top-level convenience)
# ---------------------------------------------------------------------------


class TestResolveCandidates:
    def test_returns_loadable_and_skipped(self):
        candidates = [
            _make_candidate("a"),
            _make_candidate("b", sdk=">=99.0.0"),
        ]
        loadable, skipped = resolve_candidates(candidates, "1.0.0")
        assert len(loadable) == 1
        assert len(skipped) == 1

    def test_empty_input(self):
        loadable, skipped = resolve_candidates([], "1.0.0")
        assert loadable == []
        assert skipped == []
