"""Docker sandbox manager for LLM agent code execution."""

from __future__ import annotations

import asyncio
import io
import shlex
import tarfile
from dataclasses import dataclass
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class SandboxConfig:
    container_name: str = "qqbot-sandbox"
    workspace: str = "/workspace"
    max_file_size: int = 1_048_576       # 1MB
    max_read_size: int = 524_288          # 500KB
    exec_timeout: int = 30
    max_exec_timeout: int = 60
    stdout_limit: int = 102_400           # 100KB
    enabled: bool = True


# ---------------------------------------------------------------------------
# Command blocklist – prevents agent self-destruction, not a security boundary
# ---------------------------------------------------------------------------

_BLOCKED_COMMANDS: frozenset[str] = frozenset({
    # Container escape
    "docker", "dockerd", "containerd", "runc",
    "nsenter", "unshare", "mount", "umount", "chroot",
    # Kernel interaction
    "kmod", "insmod", "rmmod", "modprobe", "dmesg", "sysctl",
    # Network changes
    "iptables", "iptables-save", "iptables-restore",
    "ip6tables", "nft",
    # Raw devices / filesystem
    "mkfs", "fdisk", "mkswap", "swapon", "swapoff",
    "losetup", "parted", "partprobe",
})

_BLOCKED_PATTERNS: tuple[str, ...] = (
    "dd if=/dev/",
    ">/dev/sd",
    "mkfs.",
    "/dev/mem",
    "/dev/kmem",
    "/dev/port",
)


# ---------------------------------------------------------------------------
# SandboxManager
# ---------------------------------------------------------------------------


class SandboxManager:
    """Manages the agent's Docker sandbox container."""

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self._client: Any = None  # docker.DockerClient
        self._container: Any = None  # docker Container
        self._exec_lock = asyncio.Lock()

    # -- lifecycle --------------------------------------------------------

    def init_client(self) -> None:
        """Create Docker client and resolve the sandbox container reference."""
        import docker

        self._client = docker.from_env()
        try:
            self._container = self._client.containers.get(self.config.container_name)
            logger.info(
                f"Sandbox container '{self.config.container_name}' "
                f"ready (status: {self._container.status})"
            )
        except docker.errors.NotFound:
            logger.warning(
                f"Sandbox container '{self.config.container_name}' not found – "
                "sandbox tools will fail until container is started"
            )

    def is_healthy(self) -> bool:
        """Check whether the sandbox container is running."""
        if self._container is None:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False

    # -- path safety -----------------------------------------------------

    def _validate_path(self, path: str) -> str:
        """Resolve *path* relative to workspace. Raise ValueError on escape.

        Uses POSIX semantics because the sandbox container is always Linux.
        """
        workspace = self.config.workspace
        # Manually resolve .. components (PurePosixPath doesn't do this)
        combined = f"{workspace}/{path}" if path else workspace
        parts = [p for p in combined.split("/") if p and p != "."]
        resolved: list[str] = []
        for part in parts:
            if part == "..":
                if resolved:
                    resolved.pop()
            else:
                resolved.append(part)
        full = "/" + "/".join(resolved)
        if full != workspace and not full.startswith(workspace + "/"):
            raise ValueError(f"Path escapes workspace: {path!r}")
        return full

    # -- command safety --------------------------------------------------

    @staticmethod
    def _check_command(command: str) -> None:
        """Reject obvious dangerous executables anywhere in a shell program.

        This is a defence-in-depth guard, not the sandbox boundary.  In
        particular, arbitrary interpreters can always construct another
        program dynamically; Docker isolation must remain effective without
        this check.
        """
        cmd = command.strip()
        if not cmd:
            raise ValueError("Empty command")
        try:
            lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError as e:
            raise ValueError(f"Invalid shell syntax: {e}") from e
        if not tokens:
            raise ValueError("Empty command")
        # Inspect every word, rather than only argv[0].  Shell programs can
        # contain pipelines, lists, substitutions and wrappers (e.g.
        # ``command /usr/bin/docker``), all of which bypassed the old check.
        for token in tokens:
            for word in token.replace("\\", "/").split():
                base = word.rsplit("/", 1)[-1]
                if base in _BLOCKED_COMMANDS:
                    raise ValueError(f"Blocked command: {base!r}")
        cmd_lower = cmd.lower()
        for pat in _BLOCKED_PATTERNS:
            if pat in cmd_lower:
                raise ValueError(f"Blocked pattern in command: {pat!r}")

    # -- exec ------------------------------------------------------------

    async def exec_cmd(self, command: str, timeout: int = 30) -> dict:
        """Execute a shell command in the sandbox container.

        Returns ``{"ok": bool, "stdout": str, "stderr": str, "exit_code": int}``.
        """
        if self._container is None:
            return {"ok": False, "error": "Sandbox container not available",
                    "stdout": "", "stderr": "", "exit_code": -1}

        timeout = min(max(timeout, 1), self.config.max_exec_timeout)
        self._check_command(command)

        # Prevent fork bombs (ulimit) and truncate output at source
        limit = self.config.stdout_limit
        # `wait_for` cannot kill a Docker exec after the client stops waiting,
        # so enforce the deadline inside the container.  Capture output before
        # truncating it so `head` cannot mask the command's exit status.
        wrapped = (
            "ulimit -u 50; out=$(mktemp) || exit 1; "
            "trap 'rm -f \"$out\"' EXIT; "
            f"timeout -s KILL {timeout}s sh -c {shlex.quote(command)} "
            '>' + '"$out" 2>&1; rc=$?; '
            f'head -c {limit + 4096} "$out"; exit "$rc"'
        )

        async with self._exec_lock:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._container.exec_run,
                        ["sh", "-c", wrapped],
                        stdout=True,
                        stderr=False,
                        tty=False,
                    ),
                    timeout=timeout + 5,
                )
            except asyncio.TimeoutError:
                return {"ok": False,
                        "error": f"Command timed out after {timeout}s",
                        "stdout": "", "stderr": "", "exit_code": -1}
            except Exception as e:
                logger.opt(exception=True).error(f"Sandbox exec failed: {e}")
                return {"ok": False, "error": str(e),
                        "stdout": "", "stderr": "", "exit_code": -1}

        exit_code = result.exit_code
        output = result.output.decode("utf-8", errors="replace") if result.output else ""
        stdout = output[:limit]
        if len(output) > limit:
            stdout += f"\n... [truncated at {limit:,} bytes]"

        logger.debug(f"Sandbox exec exit={exit_code} cmd={command[:80]!r}")
        return {"ok": exit_code == 0, "stdout": stdout, "stderr": "",
                "exit_code": exit_code}

    # -- write file ------------------------------------------------------

    async def write_file(self, path: str, content: str) -> dict:
        """Write UTF-8 text content to a file in the sandbox workspace.

        Creates parent directories automatically. Single file limit 1 MB.
        """
        if self._container is None:
            return {"ok": False, "error": "Sandbox container not available"}

        self._validate_path(path)
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.config.max_file_size:
            return {"ok": False,
                    "error": f"File too large ({len(content_bytes):,} bytes, "
                             f"max {self.config.max_file_size:,})"}

        # Ensure parent directory exists
        parent = "/".join(path.replace("\\", "/").split("/")[:-1])
        if parent:
            # Already validated via _validate_path above; compute full parent path
            parent_full = f"{self.config.workspace.rstrip('/')}/{parent}"
            mkdir_result = await self.exec_cmd(f"mkdir -p {shlex.quote(parent_full)}")
            if not mkdir_result["ok"]:
                return {"ok": False, "error": f"Failed to create parent dir: {mkdir_result['stdout']}"}

        # Build in-memory tar archive
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=path)
            info.size = len(content_bytes)
            tar.addfile(info, io.BytesIO(content_bytes))
        tar_bytes = buf.getvalue()

        async with self._exec_lock:
            try:
                await asyncio.to_thread(
                    self._container.put_archive,
                    self.config.workspace,
                    tar_bytes,
                )
            except Exception as e:
                logger.opt(exception=True).error(f"Sandbox write failed: {e}")
                return {"ok": False, "error": str(e)}

        logger.debug(f"Sandbox write: {path} ({len(content_bytes)} bytes)")
        return {"ok": True, "path": path, "size": len(content_bytes)}

    # -- read file -------------------------------------------------------

    async def read_file(self, path: str) -> dict:
        """Read text content from a file in the sandbox workspace.

        Capped at 500 KB; for larger files use ``sandbox_exec`` with
        ``head`` / ``tail`` instead.
        """
        if self._container is None:
            return {"ok": False, "error": "Sandbox container not available",
                    "content": ""}

        full_path = self._validate_path(path)
        result = await self.exec_cmd(f"cat {shlex.quote(full_path)}", timeout=10)

        if result["exit_code"] != 0:
            return {"ok": False,
                    "error": f"File not found or unreadable: {path}",
                    "content": ""}

        content = result["stdout"]
        max_read = self.config.max_read_size
        if len(content) > max_read:
            content = content[:max_read]
            content += (
                f"\n... [truncated at {max_read:,} bytes; "
                "use sandbox_exec with head/tail for large files]"
            )

        return {"ok": True, "path": path, "content": content,
                "size": len(content)}

    # -- list directory --------------------------------------------------

    async def list_dir(self, path: str = "") -> dict:
        """List files and directories under a workspace path."""
        if self._container is None:
            return {"ok": False, "error": "Sandbox container not available",
                    "listing": ""}

        full_path = self._validate_path(path) if path else self.config.workspace
        result = await self.exec_cmd(f"ls -lah {shlex.quote(full_path)}", timeout=10)

        return {"ok": result["exit_code"] == 0,
                "path": path or "/",
                "listing": result["stdout"],
                "exit_code": result["exit_code"]}

    # -- delete ----------------------------------------------------------

    async def delete_path(self, path: str) -> dict:
        """Delete a file or directory in the workspace (DESTRUCTIVE)."""
        if self._container is None:
            return {"ok": False, "error": "Sandbox container not available"}

        if not path or path in ("/", ".", "./"):
            return {"ok": False, "error": "Cannot delete root or empty path"}

        full_path = self._validate_path(path)
        if full_path == self.config.workspace:
            return {"ok": False, "error": "Cannot delete workspace root"}

        result = await self.exec_cmd(f"rm -rf {shlex.quote(full_path)}", timeout=10)

        logger.info(f"Sandbox delete: {path} (exit={result['exit_code']})")
        return {"ok": result["exit_code"] == 0, "path": path}
