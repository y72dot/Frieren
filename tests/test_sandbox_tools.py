"""Integration tests for sandbox tool executors."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sandbox_mgr() -> MagicMock:
    """A mock SandboxManager with realistic return values."""
    mgr = MagicMock()
    mgr.exec_cmd = AsyncMock(return_value={
        "ok": True, "stdout": "Hello\n", "stderr": "", "exit_code": 0,
    })
    mgr.write_file = AsyncMock(return_value={
        "ok": True, "path": "test.py", "size": 42,
    })
    mgr.read_file = AsyncMock(return_value={
        "ok": True, "path": "notes.txt", "content": "hello world",
    })
    mgr.list_dir = AsyncMock(return_value={
        "ok": True, "path": "/", "listing": "file1.py\nfile2.txt\n",
    })
    mgr.delete_path = AsyncMock(return_value={
        "ok": True, "path": "temp.txt",
    })
    return mgr


@pytest.fixture
def bot_with_sandbox(mock_api_client, mock_sandbox_mgr) -> MagicMock:
    """A mock bot with sandbox manager attached."""
    from unittest.mock import MagicMock

    bot = MagicMock()
    bot.sandbox = mock_sandbox_mgr
    bot.config = MagicMock()
    bot.config.bot.admin_users = [111]
    return bot


# ---------------------------------------------------------------------------
# sandbox_exec
# ---------------------------------------------------------------------------


class TestSandboxExec:
    @pytest.mark.asyncio
    async def test_basic(self, bot_with_sandbox):
        from plugins.llm_sandbox_tools import _exec_sandbox_exec

        result = await _exec_sandbox_exec(
            {"command": "echo hello", "timeout": 30},
            group_id=123, user_id=111, bot=bot_with_sandbox,
        )
        assert result["ok"] is True
        bot_with_sandbox.sandbox.exec_cmd.assert_called_once_with(
            command="echo hello", timeout=30,
        )

    @pytest.mark.asyncio
    async def test_default_timeout(self, bot_with_sandbox):
        from plugins.llm_sandbox_tools import _exec_sandbox_exec

        await _exec_sandbox_exec(
            {"command": "python script.py"},
            group_id=123, user_id=111, bot=bot_with_sandbox,
        )
        bot_with_sandbox.sandbox.exec_cmd.assert_called_once_with(
            command="python script.py", timeout=30,
        )


# ---------------------------------------------------------------------------
# sandbox_write
# ---------------------------------------------------------------------------


class TestSandboxWrite:
    @pytest.mark.asyncio
    async def test_basic(self, bot_with_sandbox):
        from plugins.llm_sandbox_tools import _exec_sandbox_write

        result = await _exec_sandbox_write(
            {"path": "script.py", "content": "print('hi')"},
            group_id=123, user_id=111, bot=bot_with_sandbox,
        )
        assert result["ok"] is True
        bot_with_sandbox.sandbox.write_file.assert_called_once_with(
            path="script.py", content="print('hi')",
        )


# ---------------------------------------------------------------------------
# sandbox_read
# ---------------------------------------------------------------------------


class TestSandboxRead:
    @pytest.mark.asyncio
    async def test_basic(self, bot_with_sandbox):
        from plugins.llm_sandbox_tools import _exec_sandbox_read

        result = await _exec_sandbox_read(
            {"path": "notes.txt"},
            group_id=123, user_id=111, bot=bot_with_sandbox,
        )
        assert result["ok"] is True
        bot_with_sandbox.sandbox.read_file.assert_called_once_with(
            path="notes.txt",
        )


# ---------------------------------------------------------------------------
# sandbox_list
# ---------------------------------------------------------------------------


class TestSandboxList:
    @pytest.mark.asyncio
    async def test_root(self, bot_with_sandbox):
        from plugins.llm_sandbox_tools import _exec_sandbox_list

        result = await _exec_sandbox_list(
            {},
            group_id=123, user_id=111, bot=bot_with_sandbox,
        )
        assert result["ok"] is True
        bot_with_sandbox.sandbox.list_dir.assert_called_once_with(path="")

    @pytest.mark.asyncio
    async def test_subdir(self, bot_with_sandbox):
        from plugins.llm_sandbox_tools import _exec_sandbox_list

        await _exec_sandbox_list(
            {"path": "data"},
            group_id=123, user_id=111, bot=bot_with_sandbox,
        )
        bot_with_sandbox.sandbox.list_dir.assert_called_once_with(path="data")


# ---------------------------------------------------------------------------
# sandbox_delete
# ---------------------------------------------------------------------------


class TestSandboxDelete:
    @pytest.mark.asyncio
    async def test_basic(self, bot_with_sandbox):
        from plugins.llm_sandbox_tools import _exec_sandbox_delete

        result = await _exec_sandbox_delete(
            {"path": "temp/old.txt"},
            group_id=123, user_id=111, bot=bot_with_sandbox,
        )
        assert result["ok"] is True
        bot_with_sandbox.sandbox.delete_path.assert_called_once_with(
            path="temp/old.txt",
        )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestRegisterSandboxTools:
    def test_registers_five_tools(self):
        from src.core.llm.tool_catalog import ToolCatalog
        from plugins.llm_sandbox_tools import register_sandbox_tools

        catalog = ToolCatalog()
        register_sandbox_tools(catalog)

        defs = catalog.get_all_defs()
        assert len(defs) == 5

        names = {td["function"]["name"] for td in defs}
        assert names == {
            "sandbox_exec",
            "sandbox_write",
            "sandbox_read",
            "sandbox_list",
            "sandbox_delete",
        }

    def test_delete_is_admin_only(self):
        from src.core.llm.tool_catalog import ToolCatalog
        from plugins.llm_sandbox_tools import register_sandbox_tools

        catalog = ToolCatalog()
        register_sandbox_tools(catalog)

        # sandbox_delete should be filtered for non-admins
        all_defs = catalog.get_all_defs()
        admin_defs = catalog.get_defs(user_is_admin=True)

        # All 5 should appear for admin
        assert len(admin_defs) == 5

        # Check that there are admin-only defs
        admin_only_names = {td["function"]["name"] for td in admin_defs}
        assert "sandbox_delete" in admin_only_names
