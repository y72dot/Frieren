"""Sandbox tools plugin – registers Docker-sandbox code execution tools for the LLM agent."""

from __future__ import annotations

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


async def _exec_sandbox_exec(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    """Execute an arbitrary command inside the Docker sandbox."""
    return await bot.sandbox.exec_cmd(
        command=args["command"],
        timeout=args.get("timeout", 30),
    )


async def _exec_sandbox_write(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    """Write a UTF-8 text file into the sandbox workspace."""
    return await bot.sandbox.write_file(
        path=args["path"],
        content=args["content"],
    )


async def _exec_sandbox_read(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    """Read a file from the sandbox workspace."""
    return await bot.sandbox.read_file(path=args["path"])


async def _exec_sandbox_list(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    """List directory contents in the sandbox workspace."""
    return await bot.sandbox.list_dir(path=args.get("path", ""))


async def _exec_sandbox_delete(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    """Delete a file or directory in the sandbox workspace (admin only)."""
    return await bot.sandbox.delete_path(path=args["path"])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_sandbox_tools(catalog: ToolCatalog) -> None:
    """Register the five sandbox tools into the shared :class:`ToolCatalog`."""

    catalog.register(ToolDef(
        name="sandbox_exec",
        description=(
            "在沙箱Linux容器中执行任意命令（Python、Shell等）。"
            "可以运行代码脚本、安装pip包、处理文件。"
            "每次执行独立，环境变更（pip install、文件写入）持久化到 /workspace。"
            "超时默认30秒，最长60秒。输出上限100KB。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的shell命令，如 'python script.py' 或 'pip install requests'",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数，默认30，最大60",
                },
            },
            "required": ["command"],
        },
        risk_level=RiskLevel.WRITE,
        category="sandbox",
        executor=_exec_sandbox_exec,
    ))

    catalog.register(ToolDef(
        name="sandbox_write",
        description=(
            "将UTF-8文本内容写入沙箱 /workspace 目录下的文件。"
            "自动创建父目录。单文件限制1MB。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于 /workspace 的文件路径，如 'script.py' 或 'data/notes.txt'",
                },
                "content": {
                    "type": "string",
                    "description": "UTF-8 文本内容",
                },
            },
            "required": ["path", "content"],
        },
        risk_level=RiskLevel.WRITE,
        category="sandbox",
        executor=_exec_sandbox_write,
    ))

    catalog.register(ToolDef(
        name="sandbox_read",
        description=(
            "从沙箱 /workspace 目录读取文件内容。"
            "限制500KB，超出建议用 sandbox_exec 的 head/tail 等命令分段读取。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于 /workspace 的文件路径，如 'output.txt'",
                },
            },
            "required": ["path"],
        },
        risk_level=RiskLevel.READ_ONLY,
        category="sandbox",
        executor=_exec_sandbox_read,
        cache_ttl=5.0,
    ))

    catalog.register(ToolDef(
        name="sandbox_list",
        description=(
            "列出沙箱 /workspace 目录下的文件和子目录。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于 /workspace 的子目录，默认 '' 即根目录",
                },
            },
            "required": [],
        },
        risk_level=RiskLevel.READ_ONLY,
        category="sandbox",
        executor=_exec_sandbox_list,
        cache_ttl=3.0,
    ))

    catalog.register(ToolDef(
        name="sandbox_delete",
        description=(
            "删除沙箱 /workspace 中的文件或目录。"
            "仅管理员可用。不接受空路径或根路径。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于 /workspace 的路径，如 'temp/old_data.txt'",
                },
            },
            "required": ["path"],
        },
        risk_level=RiskLevel.DESTRUCTIVE,
        category="sandbox",
        executor=_exec_sandbox_delete,
        requires_admin=True,
    ))
