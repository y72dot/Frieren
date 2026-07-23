"""Tests for PluginStorage (PLUG-502)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.plugin.context import PermissionDeniedError
from src.plugin.storage import PluginStorage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


def _perms(*scopes: str) -> list[str]:
    return list(scopes)


async def _create(plugin_id: str, perms: list[str], db_path: str) -> PluginStorage:
    return await PluginStorage.create(
        plugin_id=plugin_id,
        permissions=perms,
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# Basic KV operations
# ---------------------------------------------------------------------------


class TestBasicKV:
    async def test_set_get_roundtrip(self, db_path: str):
        """set() + get() round-trip."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set("hello", "world")
        assert await s.get("hello") == "world"

    async def test_get_returns_none_for_missing_key(self, db_path: str):
        """get() returns None for a key that was never set."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        assert await s.get("nonexistent") is None

    async def test_delete_removes_key(self, db_path: str):
        """delete() removes a key so get() returns None."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set("x", "1")
        await s.delete("x")
        assert await s.get("x") is None

    async def test_delete_nonexistent_key_is_noop(self, db_path: str):
        """delete() on a non-existent key does not raise."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.delete("ghost")  # no-op

    async def test_set_overwrites_existing_key(self, db_path: str):
        """set() overwrites a previously set key."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set("k", "v1")
        await s.set("k", "v2")
        assert await s.get("k") == "v2"

    async def test_list_keys_returns_all(self, db_path: str):
        """list_keys() returns all keys for the plugin."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set("a", "1")
        await s.set("b", "2")
        await s.set("c", "3")
        keys = await s.list_keys()
        assert keys == ["a", "b", "c"]

    async def test_list_keys_with_prefix(self, db_path: str):
        """list_keys(prefix) filters correctly."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set("config:host", "localhost")
        await s.set("config:port", "8080")
        await s.set("state:last_run", "100")
        keys = await s.list_keys("config:")
        assert sorted(keys) == ["config:host", "config:port"]

    async def test_list_keys_empty_prefix_returns_all(self, db_path: str):
        """list_keys('') returns all keys."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set("x", "1")
        assert len(await s.list_keys("")) == 1


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


class TestJsonHelpers:
    async def test_get_json_set_json_roundtrip(self, db_path: str):
        """set_json() + get_json() serialize/deserialize correctly."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set_json("data", {"a": 1, "b": [2, 3]})
        result = await s.get_json("data")
        assert result == {"a": 1, "b": [2, 3]}

    async def test_get_json_returns_none_for_missing_key(self, db_path: str):
        """get_json() returns None when key is missing."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        assert await s.get_json("ghost") is None


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    async def test_read_denied_without_read_permission(self, db_path: str):
        """get() raises PermissionDeniedError without plugin.read or plugin."""
        s = await _create("test", _perms("plugin.write"), db_path)
        with pytest.raises(PermissionDeniedError):
            await s.get("k")

    async def test_write_denied_without_write_permission(self, db_path: str):
        """set() raises PermissionDeniedError without plugin.write or plugin."""
        s = await _create("test", _perms("plugin.read"), db_path)
        with pytest.raises(PermissionDeniedError):
            await s.set("k", "v")

    async def test_delete_denied_without_write_permission(self, db_path: str):
        """delete() raises PermissionDeniedError without write permission."""
        s = await _create("test", _perms("plugin.read"), db_path)
        with pytest.raises(PermissionDeniedError):
            await s.delete("k")

    async def test_plugin_permission_grants_both(self, db_path: str):
        """'plugin' permission grants both read and write."""
        s = await _create("test", _perms("plugin"), db_path)
        await s.set("k", "v")
        assert await s.get("k") == "v"

    async def test_no_permissions_denies_all(self, db_path: str):
        """Empty permissions list denies all access."""
        s = await _create("test", [], db_path)
        with pytest.raises(PermissionDeniedError):
            await s.get("k")
        with pytest.raises(PermissionDeniedError):
            await s.set("k", "v")


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    async def test_keys_isolated_between_plugins(self, db_path: str):
        """Two plugin storages don't see each other's keys."""
        s1 = await _create("plugin_a", _perms("plugin.read", "plugin.write"), db_path)
        s2 = await _create("plugin_b", _perms("plugin.read", "plugin.write"), db_path)
        await s1.set("shared_key", "value_from_a")
        await s2.set("shared_key", "value_from_b")
        assert await s1.get("shared_key") == "value_from_a"
        assert await s2.get("shared_key") == "value_from_b"


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    async def test_default_version_is_zero(self, db_path: str):
        """Schema version defaults to 0."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        assert await s.get_schema_version() == 0

    async def test_set_and_get_schema_version(self, db_path: str):
        """set_schema_version() + get_schema_version() round-trip."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set_schema_version(3)
        assert await s.get_schema_version() == 3


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_close_cleans_up(self, db_path: str):
        """close() does not raise."""
        s = await _create("test", _perms("plugin.read", "plugin.write"), db_path)
        await s.set("k", "v")
        s.close()  # should not raise
