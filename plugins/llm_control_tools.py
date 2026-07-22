from __future__ import annotations

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef


async def _settings_get(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return {"path": args["path"], "value": bot.control_plane.get_setting(args["path"])}


async def _settings_propose(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return bot.control_plane.propose_settings(
        args["changes"], created_by=user_id, reason=args.get("reason", "")
    ).to_dict()


async def _prompts_get(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return bot.control_plane.get_prompt(args["part"])


async def _prompts_propose(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return bot.control_plane.propose_prompt(
        args["part"],
        args["content"],
        version=args["version"],
        created_by=user_id,
        reason=args.get("reason", ""),
    ).to_dict()


async def _plugins_list(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return {"plugins": bot.control_plane.list_plugins()}


async def _plugins_validate(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return bot.control_plane.validate_plugin_candidate(args["candidate"])


async def _plugins_propose_install(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return bot.control_plane.propose_plugin_install(
        args["candidate"], created_by=user_id
    ).to_dict()


async def _plugins_propose_state(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return bot.control_plane.propose_plugin_state(
        args["name"], args["enabled"], created_by=user_id
    ).to_dict()


async def _plugins_propose_rollback(args, group_id, user_id, bot):
    bot.ensure_control_plane()
    return bot.control_plane.propose_plugin_rollback(
        args["name"], created_by=user_id
    ).to_dict()


_READ_ADMIN = {"requires_admin": True}

_TOOLS = [
    ToolDef(
        name="settings_get",
        description="读取一个非敏感统一配置项；密钥、安全策略和管理员列表永不暴露",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="control",
        executor=_settings_get,
        **_READ_ADMIN,
    ),
    ToolDef(
        name="settings_propose",
        description="提出配置变更候选，不会自行批准或生效",
        parameters={
            "type": "object",
            "properties": {
                "changes": {"type": "object", "minProperties": 1},
                "reason": {"type": "string"},
            },
            "required": ["changes"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="control",
        executor=_settings_propose,
        requires_admin=True,
    ),
    ToolDef(
        name="prompts_get",
        description="读取当前 Prompt 模块",
        parameters={
            "type": "object",
            "properties": {"part": {"type": "string"}},
            "required": ["part"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="control",
        executor=_prompts_get,
        **_READ_ADMIN,
    ),
    ToolDef(
        name="prompts_propose",
        description="提出版本化 Prompt 修改候选，不会自行批准或写入文件",
        parameters={
            "type": "object",
            "properties": {
                "part": {"type": "string"},
                "content": {"type": "string", "minLength": 1},
                "version": {"type": "string", "minLength": 1},
                "reason": {"type": "string"},
            },
            "required": ["part", "content", "version"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="control",
        executor=_prompts_propose,
        requires_admin=True,
    ),
    ToolDef(
        name="plugins_list",
        description="列出当前加载插件和启用状态",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        risk_level=RiskLevel.READ_ONLY,
        category="control",
        executor=_plugins_list,
        **_READ_ADMIN,
    ),
    ToolDef(
        name="plugins_validate",
        description="静态验证 plugins/candidates 下的候选插件，不安装或执行代码",
        parameters={
            "type": "object",
            "properties": {"candidate": {"type": "string"}},
            "required": ["candidate"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ_ONLY,
        category="control",
        executor=_plugins_validate,
        **_READ_ADMIN,
    ),
    ToolDef(
        name="plugins_propose_install",
        description="为已通过静态检查的候选插件创建安装提案，不会自行安装",
        parameters={
            "type": "object",
            "properties": {"candidate": {"type": "string"}},
            "required": ["candidate"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="control",
        executor=_plugins_propose_install,
        requires_admin=True,
    ),
    ToolDef(
        name="plugins_propose_state",
        description="提出启用或禁用插件的候选，不会自行切换",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            "required": ["name", "enabled"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.WRITE,
        category="control",
        executor=_plugins_propose_state,
        requires_admin=True,
    ),
    ToolDef(
        name="plugins_propose_rollback",
        description="提出插件回滚提案；只有独立审批后才能执行",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.DESTRUCTIVE,
        category="control",
        executor=_plugins_propose_rollback,
        requires_admin=True,
    ),
]


def register_control_tools(catalog: ToolCatalog) -> None:
    for tool in _TOOLS:
        catalog.register(tool)
