"""LLM tool provider for durable QQ message artifacts."""

from __future__ import annotations

from typing import Any

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef


async def _list_message_artifacts(
    args: dict[str, Any], group_id: int | None, user_id: int, bot: Any
) -> dict[str, Any]:
    artifacts = bot.artifact_store.list_for_message(int(args["message_id"]))
    return {"artifacts": [item.to_dict() for item in artifacts]}


async def _get_artifact_info(
    args: dict[str, Any], group_id: int | None, user_id: int, bot: Any
) -> dict[str, Any]:
    artifact = bot.artifact_store.get(str(args["artifact_id"]), touch=True)
    return artifact.to_dict() if artifact else {"error": "artifact not found"}


async def _materialize_artifact(
    args: dict[str, Any], group_id: int | None, user_id: int, bot: Any
) -> dict[str, Any]:
    artifact = await bot.artifact_service.materialize(str(args["artifact_id"]))
    return artifact.to_dict()


async def _send_artifact(
    args: dict[str, Any], group_id: int | None, user_id: int, bot: Any
) -> dict[str, Any]:
    target = str(args.get("target", "current"))
    target_id = args.get("target_id")
    if target == "group":
        destination_group = int(target_id) if target_id is not None else group_id
        if destination_group is None:
            return {"error": "no group target available"}
        result = await bot.artifact_service.send(
            str(args["artifact_id"]),
            group_id=destination_group,
            name=args.get("name"),
        )
    elif target == "private":
        destination_user = int(target_id) if target_id is not None else user_id
        result = await bot.artifact_service.send(
            str(args["artifact_id"]),
            user_id=destination_user,
            name=args.get("name"),
        )
    elif group_id is not None:
        result = await bot.artifact_service.send(
            str(args["artifact_id"]), group_id=group_id, name=args.get("name")
        )
    else:
        result = await bot.artifact_service.send(
            str(args["artifact_id"]), user_id=user_id, name=args.get("name")
        )
    return {"sent": True, "result": result}


def register_artifact_tools(catalog: ToolCatalog) -> None:
    catalog.register(
        ToolDef(
            name="list_message_artifacts",
            description="列出某条 QQ 消息中保存的图片、语音、视频或文件资源。",
            parameters={
                "type": "object",
                "properties": {"message_id": {"type": "integer"}},
                "required": ["message_id"],
            },
            risk_level=RiskLevel.READ_ONLY,
            category="perception",
            executor=_list_message_artifacts,
        )
    )
    catalog.register(
        ToolDef(
            name="get_artifact_info",
            description="查询资源元数据、下载状态和本地可用性。",
            parameters={
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}},
                "required": ["artifact_id"],
            },
            risk_level=RiskLevel.READ_ONLY,
            category="perception",
            executor=_get_artifact_info,
        )
    )
    catalog.register(
        ToolDef(
            name="materialize_artifact",
            description="按需从 NapCat 获取资源并安全保存到内容寻址存储。",
            parameters={
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}},
                "required": ["artifact_id"],
            },
            risk_level=RiskLevel.WRITE,
            category="perception",
            executor=_materialize_artifact,
        )
    )
    catalog.register(
        ToolDef(
            name="send_artifact",
            description="把已保存资源作为 QQ 文件发送到当前或指定会话。",
            parameters={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "target": {
                        "type": "string",
                        "enum": ["current", "group", "private"],
                    },
                    "target_id": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["artifact_id"],
            },
            risk_level=RiskLevel.WRITE,
            category="interaction",
            executor=_send_artifact,
        )
    )
