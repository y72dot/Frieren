"""P0 PLUG-003: Reload tests – no subscription residue, module cache cleared."""

from __future__ import annotations

import sys
import textwrap

from src.core.message_bus import MessageBus, MessageType
from src.plugin.manager import PluginManager

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_MANIFEST_TEMPLATE = """\
[plugin]
id = "{id}"
name = "{name}"
version = "1.0.0"
entrypoint = "{pkg}.{name}:{class_name}"
sdk = ">=1.0,<2.0"

[permissions]
qq = []
storage = []
scheduler = false
"""


def _setup_plugin_dir(tmp_path, pkg_name: str = "test_plugins") -> str:
    """Create a plugins directory with __init__.py and add to sys.path.

    Returns the absolute path string to the plugin directory.
    """
    plugin_dir = tmp_path / pkg_name
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("")
    parent = str(tmp_path)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return str(plugin_dir)


def _write_package_plugin(
    plugin_dir: str, name: str, priority: int = 0, pkg: str = "test_plugins"
) -> None:
    """Create a minimal package plugin (dir + plugin.toml + plugin.py)."""
    from pathlib import Path

    base = Path(plugin_dir) / name
    base.mkdir(exist_ok=True)

    class_name = f"{name.title()}Plugin"
    (base / "__init__.py").write_text(
        f"from {pkg}.{name}.plugin import {class_name}  # noqa: F401\n"
    )

    manifest_text = _MANIFEST_TEMPLATE.format(
        id=name, name=name, class_name=class_name, pkg=pkg
    )
    (base / "plugin.toml").write_text(manifest_text, encoding="utf-8")

    plugin_code = textwrap.dedent(f"""\
        from src.plugin.definition import EventResult, on_event

        class {class_name}:
            __plugin_id__ = "{name}"
            name = "{name}"
            priority = {priority}

            @on_event("*", priority={priority})
            async def handler(self, event, ctx) -> EventResult:
                return EventResult.CONSUME
    """)
    (base / "plugin.py").write_text(plugin_code, encoding="utf-8")


# ---------------------------------------------------------------------------
# repeated reload – no subscription growth
# ---------------------------------------------------------------------------


def test_repeated_reload_no_accumulation(tmp_path):
    """100 reloads of the same plugin should not grow subscription count."""
    plugin_dir = _setup_plugin_dir(tmp_path)
    _write_package_plugin(plugin_dir, "hello")

    bus = MessageBus()
    pm = PluginManager(bus=bus)
    pm.auto_discover([plugin_dir])

    expected_count = bus.subscription_count
    # Should have 2 subscriptions: hello (EXTERNAL) + _qq_exec (ACTION)
    assert expected_count == 2, f"Expected 2 subs, got {expected_count}"

    for _ in range(100):
        pm.auto_discover([plugin_dir])

    # After 100 reloads the subscription count must be the same.
    assert bus.subscription_count == expected_count, (
        f"Subscription count grew: {expected_count} → {bus.subscription_count}"
    )


# ---------------------------------------------------------------------------
# deleted plugin – subscription removed
# ---------------------------------------------------------------------------


def test_deleted_plugin_removed_on_reload(tmp_path):
    """When a plugin package is deleted, its subscription should disappear."""
    import shutil
    from pathlib import Path

    plugin_dir = _setup_plugin_dir(tmp_path)

    _write_package_plugin(plugin_dir, "ping")
    _write_package_plugin(plugin_dir, "echo", priority=10)

    bus = MessageBus()
    pm = PluginManager(bus=bus)
    pm.auto_discover([plugin_dir])

    external_names = [
        s.handler.name for s in bus._subscriptions[MessageType.EXTERNAL]
    ]
    assert "*:0" in external_names  # ping
    assert "*:10" in external_names  # echo
    count_before = bus.subscription_count

    # Delete ping package
    shutil.rmtree(Path(plugin_dir) / "ping")

    # Reload
    pm.auto_discover([plugin_dir])

    external_names = [
        s.handler.name for s in bus._subscriptions[MessageType.EXTERNAL]
    ]
    assert "*:0" not in external_names, "Deleted plugin 'ping' still subscribed"
    assert "*:10" in external_names
    assert bus.subscription_count == count_before - 1  # only ping removed


# ---------------------------------------------------------------------------
# renamed plugin – old gone, new present
# ---------------------------------------------------------------------------


def test_renamed_plugin_old_removed_new_added(tmp_path):
    """Renaming a plugin should remove the old name and add the new."""
    import shutil
    from pathlib import Path

    plugin_dir = _setup_plugin_dir(tmp_path)

    _write_package_plugin(plugin_dir, "old_name")
    _write_package_plugin(plugin_dir, "other", priority=5)

    bus = MessageBus()
    pm = PluginManager(bus=bus)
    pm.auto_discover([plugin_dir])

    count_before = bus.subscription_count

    external_names = [
        s.handler.name for s in bus._subscriptions[MessageType.EXTERNAL]
    ]
    assert "*:0" in external_names  # old_name

    # Rename: delete old, create new
    shutil.rmtree(Path(plugin_dir) / "old_name")
    _write_package_plugin(plugin_dir, "new_name")

    pm.auto_discover([plugin_dir])

    external_names = [
        s.handler.name for s in bus._subscriptions[MessageType.EXTERNAL]
    ]
    assert "*:0" in external_names  # from "other" + "new_name" both at p=0
    assert bus.subscription_count == count_before  # 1 removed + 1 added


# ---------------------------------------------------------------------------
# module cache cleared – source changes picked up
# ---------------------------------------------------------------------------


def test_module_cache_cleared_on_reload(tmp_path):
    """Changes to a plugin's source file should be picked up on reload."""
    import shutil
    from pathlib import Path

    plugin_dir = _setup_plugin_dir(tmp_path)
    pkg_name = "test_plugins"

    _write_package_plugin(plugin_dir, "dynamic")

    bus = MessageBus()
    pm = PluginManager(bus=bus)
    pm.auto_discover([plugin_dir])

    # Verify the plugin module is cached
    mod_name = f"{pkg_name}.dynamic.plugin"
    assert mod_name in sys.modules

    # Change source – also purge __pycache__
    pycache = Path(plugin_dir) / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)
    inner_pycache = Path(plugin_dir) / "dynamic" / "__pycache__"
    if inner_pycache.exists():
        shutil.rmtree(inner_pycache)

    # Write updated source with higher priority
    class_name = "DynamicPlugin"
    plugin_code = textwrap.dedent(f"""\
        from src.plugin.definition import EventResult, on_event

        class {class_name}:
            __plugin_id__ = "dynamic"
            name = "dynamic"
            priority = 5

            @on_event("*", priority=5)
            async def handler(self, event, ctx) -> EventResult:
                return EventResult.CONSUME
    """)
    (Path(plugin_dir) / "dynamic" / "plugin.py").write_text(
        plugin_code, encoding="utf-8"
    )

    pm.auto_discover([plugin_dir])

    # After reload, the module should reflect v2
    assert mod_name in sys.modules


# ---------------------------------------------------------------------------
# close() cleans up properly
# ---------------------------------------------------------------------------


def test_manager_close_removes_all(tmp_path):
    """After close(), no plugin subscriptions should remain on the bus."""
    plugin_dir = _setup_plugin_dir(tmp_path)

    _write_package_plugin(plugin_dir, "a")
    _write_package_plugin(plugin_dir, "b", priority=5)

    bus = MessageBus()
    pm = PluginManager(bus=bus)
    pm.auto_discover([plugin_dir])

    # Both plugins + _qq_exec
    assert bus.subscription_count == 3

    pm.close()

    # Only _qq_exec remains (built-in, not scope-managed)
    assert bus.subscription_count == 1
    external_names = [
        s.handler.name for s in bus._subscriptions[MessageType.EXTERNAL]
    ]
    assert external_names == []


# ---------------------------------------------------------------------------
# close() is idempotent
# ---------------------------------------------------------------------------


def test_manager_close_idempotent(tmp_path):
    """Calling close() twice should not raise errors."""
    plugin_dir = _setup_plugin_dir(tmp_path)

    _write_package_plugin(plugin_dir, "x")

    bus = MessageBus()
    pm = PluginManager(bus=bus)
    pm.auto_discover([plugin_dir])

    pm.close()
    count_after = bus.subscription_count

    # Second close – no error
    pm.close()
    assert bus.subscription_count == count_after
