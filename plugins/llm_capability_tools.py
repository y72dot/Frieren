from __future__ import annotations

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef


async def _search(domain: str, args: dict, group_id, user_id, bot) -> dict:
    bot.ensure_capability_services()
    filters = {}
    if domain == "messages":
        filters = {
            "conversation_type": "group" if group_id is not None else "private",
            "conversation_id": group_id if group_id is not None else user_id,
        }
    return bot.search_service.search(
        domain, args["query"], limit=args.get("limit", 20), **filters
    )


async def _search_messages(args, group_id, user_id, bot):
    return await _search("messages", args, group_id, user_id, bot)


async def _search_artifacts(args, group_id, user_id, bot):
    return await _search("artifacts", args, group_id, user_id, bot)


async def _search_workspace(args, group_id, user_id, bot):
    return await _search("workspace", args, group_id, user_id, bot)


async def _search_tasks(args, group_id, user_id, bot):
    return await _search("tasks", args, group_id, user_id, bot)


async def _search_memory(args, group_id, user_id, bot):
    return await _search("memory", args, group_id, user_id, bot)


async def _workspace_write(args, group_id, user_id, bot):
    bot.ensure_capability_services()
    result = bot.workspace.write_text(
        args["path"], args["content"], overwrite=args.get("overwrite", False)
    )
    if args.get("export_artifact", False):
        result["artifact"] = bot.workspace.export_artifact(args["path"]).to_dict()
    return result


async def _workspace_read(args, group_id, user_id, bot):
    bot.ensure_capability_services()
    return bot.workspace.read_text(args["path"])


async def _workspace_list(args, group_id, user_id, bot):
    bot.ensure_capability_services()
    return {"entries": [item.__dict__ for item in bot.workspace.list(args.get("path", ""))]}


async def _workspace_export(args, group_id, user_id, bot):
    bot.ensure_capability_services()
    return bot.workspace.export_artifact(args["path"]).to_dict()


async def _web_search(args, group_id, user_id, bot):
    bot.ensure_capability_services()
    results = await bot.web_client.search(args["query"], limit=args.get("limit", 10))
    return {"results": [item.__dict__ for item in results], "untrusted": True}


async def _web_fetch(args, group_id, user_id, bot):
    bot.ensure_capability_services()
    document = await bot.web_client.fetch(args["url"])
    value = document.to_dict()
    value["text"] = value["text"][: args.get("max_chars", 12000)]
    return value


async def _web_download(args, group_id, user_id, bot):
    bot.ensure_capability_services()
    return await bot.web_client.download(args["url"], file_name=args.get("file_name"))


def _search_tool(name: str, description: str, executor, *, admin: bool = False) -> ToolDef:
    return ToolDef(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="search",
        executor=executor,
        requires_admin=admin,
    )


_TOOLS = [
    _search_tool("search_messages", "搜索当前 QQ 会话的本地消息，返回可追溯 message 引用", _search_messages),
    _search_tool("search_artifacts", "搜索 Bot 已归档的文件和网页 Artifact", _search_artifacts, admin=True),
    _search_tool("search_workspace", "搜索 Bot 本地工作区文件名和文本", _search_workspace, admin=True),
    _search_tool("search_tasks", "搜索持久化任务、状态和任务模板", _search_tasks, admin=True),
    _search_tool("search_memory", "搜索情景记忆和语义事实", _search_memory, admin=True),
    ToolDef(
        name="workspace_write",
        description="在 Bot 受控工作区原子创建或覆盖 UTF-8 文件，可同时导出为 Artifact",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "export_artifact": {"type": "boolean"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="workspace",
        executor=_workspace_write,
        requires_admin=True,
    ),
    ToolDef(
        name="workspace_read",
        description="读取 Bot 受控工作区内的 UTF-8 文件",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="workspace",
        executor=_workspace_read,
        requires_admin=True,
    ),
    ToolDef(
        name="workspace_list",
        description="列出 Bot 受控工作区目录",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="workspace",
        executor=_workspace_list,
        requires_admin=True,
    ),
    ToolDef(
        name="workspace_export_artifact",
        description="把工作区文件导入内容寻址 Artifact Store，以便发送到 QQ",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="workspace",
        executor=_workspace_export,
        requires_admin=True,
    ),
    ToolDef(
        name="web_search",
        description="搜索公开网页，只返回搜索结果，不自动访问结果页面",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="web",
        executor=_web_search,
        requires_admin=True,
    ),
    ToolDef(
        name="web_fetch",
        description="安全抓取一个公开 HTTP/HTTPS 页面并归档为不可信 Artifact",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1},
                "max_chars": {"type": "integer", "minimum": 100, "maximum": 50000},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="web",
        executor=_web_fetch,
        requires_admin=True,
    ),
    ToolDef(
        name="web_download",
        description="安全下载公开 URL 并写入 Artifact Store，不执行下载内容",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1},
                "file_name": {"type": "string"},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="web",
        executor=_web_download,
        requires_admin=True,
    ),
]


def register_capability_tools(catalog: ToolCatalog) -> None:
    for tool in _TOOLS:
        catalog.register(tool)
