"""Unit tests for SandboxManager – path validation, blocklist, exec/write/read ops."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.llm.sandbox_manager import SandboxConfig, SandboxManager


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> SandboxConfig:
    return SandboxConfig(
        container_name="test-sandbox",
        workspace="/workspace",
        max_file_size=1_048_576,
        max_read_size=524_288,
        exec_timeout=30,
        max_exec_timeout=60,
        stdout_limit=102_400,
        enabled=True,
    )


@pytest.fixture
def mgr(config: SandboxConfig) -> SandboxManager:
    return SandboxManager(config)


# ---------------------------------------------------------------------------
# path validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_simple_relative_path(self, mgr: SandboxManager):
        assert mgr._validate_path("script.py") == "/workspace/script.py"

    def test_nested_path(self, mgr: SandboxManager):
        assert mgr._validate_path("data/notes.txt") == "/workspace/data/notes.txt"

    def test_parent_dir_traversal(self, mgr: SandboxManager):
        with pytest.raises(ValueError, match="escapes workspace"):
            mgr._validate_path("../../../etc/passwd")

    def test_empty_path(self, mgr: SandboxManager):
        result = mgr._validate_path("")
        assert result in ("/workspace", "/workspace/")

    def test_dot_path(self, mgr: SandboxManager):
        result = mgr._validate_path(".")
        assert result in ("/workspace", "/workspace/")


# ---------------------------------------------------------------------------
# command blocklist
# ---------------------------------------------------------------------------


class TestCommandBlocklist:
    def test_docker_blocked(self, mgr: SandboxManager):
        with pytest.raises(ValueError, match="Blocked command"):
            mgr._check_command("docker ps")

    def test_nsenter_blocked(self, mgr: SandboxManager):
        with pytest.raises(ValueError, match="Blocked command"):
            mgr._check_command("nsenter -t 1 bash")

    def test_mount_blocked(self, mgr: SandboxManager):
        with pytest.raises(ValueError, match="Blocked command"):
            mgr._check_command("mount /dev/sda1 /mnt")

    def test_iptables_blocked(self, mgr: SandboxManager):
        with pytest.raises(ValueError, match="Blocked command"):
            mgr._check_command("iptables -L")

    def test_dd_dev_blocked(self, mgr: SandboxManager):
        with pytest.raises(ValueError, match="Blocked pattern"):
            mgr._check_command("dd if=/dev/sda of=backup.img")

    @pytest.mark.parametrize("command", [
        "echo ok; docker ps",
        "echo ok | /usr/bin/nsenter -t 1 sh",
        "command /usr/bin/mount /dev/sda /mnt",
        "echo $(docker ps)",
        "sh -c 'docker ps'",
    ])
    def test_blocked_command_cannot_hide_later_in_shell_program(
        self, mgr: SandboxManager, command: str,
    ):
        with pytest.raises(ValueError, match="Blocked command"):
            mgr._check_command(command)

    def test_empty_command(self, mgr: SandboxManager):
        with pytest.raises(ValueError, match="Empty"):
            mgr._check_command("")

    def test_python_allowed(self, mgr: SandboxManager):
        mgr._check_command("python script.py")  # no exception

    def test_pip_allowed(self, mgr: SandboxManager):
        mgr._check_command("pip install requests")  # no exception

    def test_ls_allowed(self, mgr: SandboxManager):
        mgr._check_command("ls -la /workspace")  # no exception


# ---------------------------------------------------------------------------
# exec_cmd – unit tests with mocked Docker
# ---------------------------------------------------------------------------


class TestExecCmd:
    @pytest.mark.asyncio
    async def test_basic_exec(self, mgr: SandboxManager):
        mock_container = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.output = b"Hello, World!\n"
        mock_container.exec_run.return_value = mock_result
        mgr._container = mock_container

        result = await mgr.exec_cmd("echo Hello, World!")
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert "Hello, World!" in result["stdout"]

    @pytest.mark.asyncio
    async def test_command_failure(self, mgr: SandboxManager):
        mock_container = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.output = b"Error: file not found\n"
        mock_container.exec_run.return_value = mock_result
        mgr._container = mock_container

        result = await mgr.exec_cmd("cat /nonexistent")
        assert result["ok"] is False
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_no_container(self, mgr: SandboxManager):
        result = await mgr.exec_cmd("echo hi")
        assert result["ok"] is False
        assert "not available" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_blocked_command(self, mgr: SandboxManager):
        mgr._container = MagicMock()  # needed so exec_cmd reaches _check_command
        with pytest.raises(ValueError, match="Blocked"):
            await mgr.exec_cmd("docker ps")

    @pytest.mark.asyncio
    async def test_timeout_capped(self, mgr: SandboxManager):
        """timeout > max_exec_timeout should be capped."""
        mock_container = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.output = b"done\n"
        mock_container.exec_run.return_value = mock_result
        mgr._container = mock_container

        # Request 999 but should be capped to max_exec_timeout (60)
        result = await mgr.exec_cmd("sleep 90", timeout=999)
        mock_container.exec_run.assert_called_once()
        args, _kwargs = mock_container.exec_run.call_args
        # The timeout passed to asyncio.wait_for is timeout+5 = cap+5 = 65
        assert result["ok"] is True  # command completed in mock

        wrapped = mock_container.exec_run.call_args.args[0][2]
        assert "timeout -s KILL 60s" in wrapped

    @pytest.mark.asyncio
    async def test_exec_preserves_failure_through_output_pipe(self, mgr: SandboxManager):
        mock_container = MagicMock()
        mock_result = MagicMock(exit_code=7, output=b"failed\n")
        mock_container.exec_run.return_value = mock_result
        mgr._container = mock_container

        result = await mgr.exec_cmd("false")

        assert result["ok"] is False
        command = mock_container.exec_run.call_args.args[0]
        assert command[:2] == ["sh", "-c"]
        assert 'exit "$rc"' in command[2]

    @pytest.mark.asyncio
    async def test_output_truncation(self, mgr: SandboxManager):
        mock_container = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        # Generate output larger than stdout_limit
        long_output = b"x" * (mgr.config.stdout_limit + 5000)
        mock_result.output = long_output
        mock_container.exec_run.return_value = mock_result
        mgr._container = mock_container

        result = await mgr.exec_cmd("python -c 'print(\"x\"*200000)'")
        assert "truncated" in result["stdout"]
        assert len(result["stdout"]) <= mgr.config.stdout_limit + 200  # + truncation msg


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_simple_file(self, mgr: SandboxManager):
        mock_container = MagicMock()
        # exec (mkdir) result
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.output = b""
        mock_container.exec_run.return_value = exec_result
        mock_container.put_archive.return_value = True
        mgr._container = mock_container

        result = await mgr.write_file("test.py", "print('hello')")
        assert result["ok"] is True
        assert result["path"] == "test.py"
        assert result["size"] > 0

    @pytest.mark.asyncio
    async def test_write_too_large(self, mgr: SandboxManager):
        mgr._container = MagicMock()
        mgr.config.max_file_size = 10
        result = await mgr.write_file("big.txt", "x" * 100)
        assert result["ok"] is False
        assert "too large" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_path_escape_rejected(self, mgr: SandboxManager):
        mgr._container = MagicMock()
        with pytest.raises(ValueError, match="escapes"):
            await mgr.write_file("../../../etc/passwd", "bad")

    @pytest.mark.asyncio
    async def test_no_container(self, mgr: SandboxManager):
        result = await mgr.write_file("f.py", "code")
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_file(self, mgr: SandboxManager):
        mock_container = MagicMock()
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.output = b"file content here\n"
        mock_container.exec_run.return_value = exec_result
        mgr._container = mock_container

        result = await mgr.read_file("notes.txt")
        assert result["ok"] is True
        assert result["content"] == "file content here\n"

    @pytest.mark.asyncio
    async def test_read_truncation(self, mgr: SandboxManager):
        mock_container = MagicMock()
        exec_result = MagicMock()
        exec_result.exit_code = 0
        long_content = b"y" * (mgr.config.max_read_size + 1000)
        exec_result.output = long_content
        mock_container.exec_run.return_value = exec_result
        mgr._container = mock_container

        result = await mgr.read_file("big.txt")
        assert result["ok"] is True
        assert "truncated" in result["content"]

    @pytest.mark.asyncio
    async def test_read_not_found(self, mgr: SandboxManager):
        mock_container = MagicMock()
        exec_result = MagicMock()
        exec_result.exit_code = 1
        exec_result.output = b"No such file\n"
        mock_container.exec_run.return_value = exec_result
        mgr._container = mock_container

        result = await mgr.read_file("missing.txt")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_no_container(self, mgr: SandboxManager):
        result = await mgr.read_file("x.txt")
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


class TestListDir:
    @pytest.mark.asyncio
    async def test_list_root(self, mgr: SandboxManager):
        mock_container = MagicMock()
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.output = b"total 8\ndrwxr-xr-x 2 root root 4096 Jan 1 00:00 .\n"
        mock_container.exec_run.return_value = exec_result
        mgr._container = mock_container

        result = await mgr.list_dir()
        assert result["ok"] is True
        assert result["path"] == "/"

    @pytest.mark.asyncio
    async def test_list_subdir(self, mgr: SandboxManager):
        mock_container = MagicMock()
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.output = b"drwxr-xr-x 2 root root 4096 Jan 1 00:00 data\n"
        mock_container.exec_run.return_value = exec_result
        mgr._container = mock_container

        result = await mgr.list_dir("data")
        assert result["ok"] is True
        assert result["path"] == "data"


# ---------------------------------------------------------------------------
# delete_path
# ---------------------------------------------------------------------------


class TestDeletePath:
    @pytest.mark.asyncio
    async def test_delete_file(self, mgr: SandboxManager):
        mock_container = MagicMock()
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.output = b""
        mock_container.exec_run.return_value = exec_result
        mgr._container = mock_container

        result = await mgr.delete_path("temp/old.txt")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_delete_root_rejected(self, mgr: SandboxManager):
        mgr._container = MagicMock()
        result = await mgr.delete_path("")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_delete_slash_rejected(self, mgr: SandboxManager):
        mgr._container = MagicMock()
        result = await mgr.delete_path("/")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_delete_workspace_root_rejected(self, mgr: SandboxManager):
        mgr._container = MagicMock()
        # path "." resolves to /workspace which == config.workspace
        result = await mgr.delete_path(".")
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------


class TestIsHealthy:
    def test_no_container(self, mgr: SandboxManager):
        assert mgr.is_healthy() is False

    def test_container_running(self, mgr: SandboxManager):
        mock_container = MagicMock()
        mock_container.status = "running"
        mgr._container = mock_container

        assert mgr.is_healthy() is True

    def test_container_stopped(self, mgr: SandboxManager):
        mock_container = MagicMock()
        mock_container.status = "exited"
        mgr._container = mock_container

        assert mgr.is_healthy() is False

    def test_container_error(self, mgr: SandboxManager):
        mock_container = MagicMock()
        mock_container.reload.side_effect = RuntimeError("docker down")
        mgr._container = mock_container

        assert mgr.is_healthy() is False
