"""Plugin CLI – scaffolding, validation, listing, and diagnostics.

Usage::

    python -m src.plugin.cli new <name>
    python -m src.plugin.cli validate <path>
    python -m src.plugin.cli list
    python -m src.plugin.cli doctor
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_PLUGIN_TOML_TMPL = """\
[plugin]
id = "{name}"
name = "{Name}"
version = "0.1.0"
entrypoint = "plugins.{name}.plugin:{ClassName}"
sdk = ">=1.0,<2.0"
description = "{Name} plugin"

[permissions]
qq = ["message.send"]
storage = ["plugin"]
scheduler = false
"""

_PLUGIN_PY_TMPL = '''\
"""Plugin: {Name}"""

from src.plugin import EventResult, command


class {ClassName}:
    __plugin_id__ = "{name}"

    @command("/{name}")
    async def cmd_{name}(self, event, ctx) -> EventResult:
        await ctx.reply(event, "Hello from {Name}!")
        return EventResult.CONSUME
'''

_TEST_PY_TMPL = '''\
"""Tests for {Name} plugin."""

import pytest


@pytest.mark.asyncio
async def test_{name}_command():
    """Stub test – replace with real logic."""
    assert True
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snake_to_pascal(name: str) -> str:
    """Convert snake_case to PascalCase (e.g. ``hello_world`` → ``HelloWorld``)."""
    return name.replace("_", " ").title().replace(" ", "")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_new(args: argparse.Namespace) -> int:
    name = args.name
    # Validate plugin id format.
    import re
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        print(f"ERROR: plugin name '{name}' must be lowercase snake_case (letters, digits, underscores; start with a letter)")
        return 1

    plugin_dir = Path("plugins") / name
    if plugin_dir.exists():
        print(f"ERROR: directory already exists: {plugin_dir}")
        return 1

    plugin_dir.mkdir(parents=True)
    class_name = _snake_to_pascal(name)
    title_name = class_name

    # plugin.toml
    (plugin_dir / "plugin.toml").write_text(
        _PLUGIN_TOML_TMPL.format(name=name, Name=title_name, ClassName=class_name),
        encoding="utf-8",
    )
    # plugin.py
    (plugin_dir / "plugin.py").write_text(
        _PLUGIN_PY_TMPL.format(name=name, Name=title_name, ClassName=class_name),
        encoding="utf-8",
    )
    # __init__.py
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")

    # tests
    tests_dir = Path("tests")
    test_path = tests_dir / f"test_{name}.py"
    if not test_path.exists():
        tests_dir.mkdir(parents=True, exist_ok=True)
        test_path.write_text(
            _TEST_PY_TMPL.format(name=name, Name=title_name),
            encoding="utf-8",
        )

    print(f"Created plugin '{name}' at {plugin_dir}/")
    print("  plugin.toml")
    print("  plugin.py")
    print("  __init__.py")
    if test_path.exists():
        print(f"  {test_path}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if not target.exists():
        print(f"ERROR: path not found: {target}")
        return 1

    if target.is_dir():
        target = target / "plugin.toml"
        if not target.exists():
            print(f"ERROR: plugin.toml not found in {args.path}")
            return 1

    if target.suffix != ".toml":
        print(f"ERROR: expected a .toml file, got {target}")
        return 1

    try:
        from src.plugin.manifest import ManifestError, parse_manifest
        manifest = parse_manifest(str(target))
        print(f"OK: plugin '{manifest.id}' v{manifest.version}")
        print(f"  entrypoint: {manifest.entrypoint}")
        print(f"  sdk: {manifest.sdk}")
        if manifest.dependencies:
            print(f"  dependencies: {', '.join(manifest.dependencies)}")
        if manifest.config and manifest.config.schema:
            print(f"  config schema: {manifest.config.schema}")
        return 0
    except ManifestError as exc:
        print(f"FAIL: {exc}")
        return 1


def _cmd_list(args: argparse.Namespace) -> int:
    from src.plugin.loader import discover_candidates

    plugin_dirs = args.plugin_dirs.split(",") if args.plugin_dirs else ["plugins"]
    candidates = discover_candidates(plugin_dirs)

    if not candidates:
        print("No plugins found.")
        return 0

    print(f"{'PLUGIN':<24} {'TYPE':<8} {'VERSION':<10} {'PATH'}")
    print("-" * 72)
    for c in sorted(candidates, key=lambda x: x.plugin_id):
        ltype = c.loader_type.value
        ver = c.manifest.version if c.manifest else "-"
        path = str(c.path)
        print(f"{c.plugin_id:<24} {ltype:<8} {ver:<10} {path}")
    print(f"\nTotal: {len(candidates)} plugin(s)")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run comprehensive diagnostics on the plugin system."""
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    # 1. Discover plugins.
    from src.plugin.loader import discover_candidates

    plugin_dirs = args.plugin_dirs.split(",") if args.plugin_dirs else ["plugins"]
    try:
        candidates = discover_candidates(plugin_dirs)
        info.append(f"Discovery: {len(candidates)} candidate(s) found in {plugin_dirs}")
    except Exception as exc:
        errors.append(f"Discovery failed: {exc}")
        candidates = []

    # 2. Validate each manifest.
    from src.plugin.manifest import ManifestError, parse_manifest

    for c in candidates:
        if c.loader_type.value == "package" and c.path:
            toml_path = c.path / "plugin.toml" if c.path.is_dir() else c.path
            try:
                manifest = parse_manifest(str(toml_path))
                info.append(
                    f"  {manifest.id}: v{manifest.version}, "
                    f"entrypoint={manifest.entrypoint}"
                )
                # Check config schema if declared.
                if manifest.config and manifest.config.schema:
                    from src.plugin.config import load_schema

                    try:
                        load_schema(manifest.config.schema)
                        info.append(f"    config schema: OK ({manifest.config.schema})")
                    except Exception as exc:
                        warnings.append(
                            f"  {manifest.id}: config schema error: {exc}"
                        )
            except ManifestError as exc:
                errors.append(f"  {c.plugin_id}: manifest error: {exc}")

    # 3. Dependency graph check.
    if len(candidates) >= 2:
        try:
            from src.plugin import SDK_VERSION
            from src.plugin.topology import resolve_candidates

            loadable, skipped = resolve_candidates(candidates, SDK_VERSION)
            info.append(f"Topology: {len(loadable)} loadable, {len(skipped)} skipped")
            for c, reason in skipped:
                warnings.append(f"  {c.plugin_id}: skipped — {reason}")
        except Exception as exc:
            errors.append(f"Topology resolution failed: {exc}")

    # 4. Print report.
    print("=== Plugin Doctor Report ===\n")
    for line in info:
        print(f"[INFO] {line}")
    for line in warnings:
        print(f"[WARN] {line}")
    for line in errors:
        print(f"[ERROR] {line}")

    if errors:
        print(f"\n{len(errors)} error(s) found.")
        return 1
    if warnings:
        print(f"\n{len(warnings)} warning(s).")
    else:
        print("\nAll checks passed.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.plugin.cli",
        description="QQBot Plugin CLI – scaffolding, validation, diagnostics",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # new
    p_new = sub.add_parser("new", help="Scaffold a new plugin package")
    p_new.add_argument("name", help="Plugin id (lowercase snake_case)")

    # validate
    p_val = sub.add_parser("validate", help="Validate a plugin.toml or plugin directory")
    p_val.add_argument("path", help="Path to plugin.toml or plugin directory")

    # list
    p_list = sub.add_parser("list", help="List discovered plugins")
    p_list.add_argument(
        "--plugin-dirs", default="plugins",
        help="Comma-separated plugin directories (default: plugins)"
    )

    # doctor
    p_doc = sub.add_parser("doctor", help="Run comprehensive plugin diagnostics")
    p_doc.add_argument(
        "--plugin-dirs", default="plugins",
        help="Comma-separated plugin directories (default: plugins)"
    )

    args = parser.parse_args(argv)

    if args.command == "new":
        return _cmd_new(args)
    elif args.command == "validate":
        return _cmd_validate(args)
    elif args.command == "list":
        return _cmd_list(args)
    elif args.command == "doctor":
        return _cmd_doctor(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
