"""LLM tool provider for Docker-sandbox code execution."""

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


async def _exec_sandbox_delete(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    """Delete a file or directory in the sandbox workspace (admin only)."""
    return await bot.sandbox.delete_path(path=args["path"])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_sandbox_tools(catalog: ToolCatalog) -> None:
    """Register the compact sandbox tools into the shared catalog."""

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
