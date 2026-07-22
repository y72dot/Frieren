"""LLM tool provider for QQ interaction, queries, and moderation."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.core.llm.message_format import format_message
from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef
from src.core.message_store import StoredMessage

# ---------------------------------------------------------------------------
# Individual tool executors (split from the old _execute if/elif chain)
# ---------------------------------------------------------------------------


async def _exec_set_essence(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    if args.get("enabled", True):
        return await bot.api.set_essence_msg(args["message_id"])
    return await bot.api.delete_essence_msg(args["message_id"])


async def _exec_react_emoji(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.call_action(
        "set_msg_emoji_like",
        message_id=args["message_id"],
        emoji_id=args["emoji_id"],
        set=args.get("enabled", True),
    )


async def _exec_send_message(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    if group_id:
        await bot.api.send_group_msg(group_id, args["text"])
    else:
        await bot.api.send_private_msg(user_id, args["text"])
    return {"sent": True}


async def _exec_mute_user(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.set_group_ban(group_id, args["user_id"], args["duration"])


async def _exec_kick_user(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.set_group_kick(group_id, args["user_id"])


async def _exec_get_current_time(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    import datetime
    return {"datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


async def _exec_query_history(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    import datetime as _dt
    import time as _time

    utc_offset = -_time.timezone if not _time.daylight else -_time.altzone

    def _parse_dt(s: str) -> int:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = _dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"无法解析时间: {s!r}")
        delta = dt - _dt.datetime(1970, 1, 1)
        return int(delta.total_seconds() - utc_offset)

    msg_id = args.get("message_id")
    keyword = args.get("keyword")
    uid = args.get("user_id")
    limit = min(args.get("limit", 30), 50)
    time_after = args.get("time_after")
    time_before = args.get("time_before")
    bot_scope = args.get("bot_scope", "include")

    kwargs: dict = {"n": limit}
    if group_id:
        kwargs["group_id"] = group_id
        kwargs["is_group"] = True
    else:
        kwargs["is_group"] = False

    if msg_id is not None:
        kwargs["message_id"] = msg_id
    if keyword:
        kwargs["keyword"] = keyword
    if uid is not None:
        if bot_scope == "only" and uid != bot.config.bot.qq:
            return {"text": "参数冲突: bot_scope=only 时 user_id 只能是机器人自己的QQ号"}
        kwargs["user_id"] = uid
    if time_after is not None:
        kwargs["time_after"] = _parse_dt(str(time_after))
    if time_before is not None:
        kwargs["time_before"] = _parse_dt(str(time_before))

    if bot_scope == "only":
        kwargs["user_id"] = bot.config.bot.qq
    elif bot_scope == "exclude":
        kwargs["exclude_user_ids"] = [bot.config.bot.qq]

    coverage = "unknown"
    gaps: list[dict] = []
    history_query = getattr(bot, "history_query", None)
    if history_query is not None:
        ensure_history = getattr(bot, "ensure_history_services", None)
        if ensure_history is not None:
            ensure_history()
            history_query = bot.history_query
        conversation_type = "group" if group_id else "private"
        conversation_id = group_id if group_id else user_id
        criteria = {
            key: value
            for key, value in kwargs.items()
            if key not in {"group_id", "is_group"}
        }
        result = await history_query.query(
            conversation_type, int(conversation_id or 0), **criteria
        )
        msgs = result.messages
        coverage = result.coverage
        gaps = result.gaps
    else:
        msgs = bot.msg_store.query(**kwargs)

    has_other_filters = bool(keyword or uid is not None or time_after is not None or time_before is not None)
    if not msgs and msg_id is not None and not has_other_filters:
        try:
            quiet_call = getattr(bot.api, "call_action_quiet", None)
            raw = (
                await quiet_call("get_msg", message_id=msg_id)
                if quiet_call is not None
                else await bot.api.get_msg(msg_id)
            )
            if raw.get("status") == "failed":
                raise LookupError(raw.get("message") or "message unavailable")
            data = raw.get("data", raw)
            sender = data.get("sender", {})
            _uid = sender.get("user_id", 0)
            _nick = sender.get("nickname", "") or sender.get("card", "")
            _content = data.get("message", data.get("raw_message", "")) or json.dumps(data, ensure_ascii=False)
            _time_val = data.get("time", 0)
            msgs = [StoredMessage(
                message_id=msg_id, user_id=_uid, nickname=_nick,
                content=str(_content), time=_time_val, group_id=group_id,
            )]
        except Exception:
            pass

    if not msgs:
        return {"text": "没有找到相关消息。", "coverage": coverage, "gaps": gaps}

    lines = [
        format_message(m, bot.config.bot.qq, include_time=True)
        for m in msgs
        if m.content.strip()
    ]
    return {
        "text": "找到以下消息：\n" + "\n".join(lines),
        "coverage": coverage,
        "gaps": gaps,
    }


async def _exec_get_group_info(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    result = await bot.api.get_group_info(group_id)
    data = result.get("data", result)
    info_fields = {}
    for k in ("group_name", "group_id", "member_count", "max_member_count", "create_time", "group_level", "owner_id", "admin_count", "group_memo"):
        if k in data:
            info_fields[k] = data[k]
    if not info_fields:
        info_fields = data
    return {"text": json.dumps(info_fields, ensure_ascii=False, indent=2)}


async def _exec_get_member_info(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    result = await bot.api.get_group_member_info(group_id, args["user_id"])
    data = result.get("data", result)
    fields = {}
    for k in ("user_id", "nickname", "card", "role", "title", "join_time", "last_speak_time", "level"):
        if k in data:
            fields[k] = data[k]
    if not fields:
        fields = data
    return {"text": json.dumps(fields, ensure_ascii=False, indent=2)}


async def _exec_get_member_list(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    result = await bot.api.get_group_member_list(group_id)
    data = result.get("data", result)
    members = data if isinstance(data, list) else data.get("members", [])
    total = len(members)
    role_labels = {"owner": "[群主]", "admin": "[管理员]", "member": ""}
    lines: list[str] = []
    for m in members[:100]:
        uid = m.get("user_id", 0)
        nickname = m.get("nickname", "") or ""
        card = m.get("card", "") or ""
        role = m.get("role", "member")
        role_tag = role_labels.get(role, f"[{role}]")
        display = f"{nickname}({uid})"
        if card and card != nickname:
            display += f" 群名片:{card}"
        if role_tag:
            display += f" {role_tag}"
        lines.append(display)
    header = f"群成员共 {total} 人："
    text = header + "\n" + "\n".join(lines)
    if total > 100:
        text += f"\n...（仅显示前100人，共{total}人）"
    return {"text": text}


async def _exec_get_essence_list(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    raw = await bot.api.call_action("get_essence_msg_list", group_id=group_id)
    data = raw.get("data", raw)
    essences = data if isinstance(data, list) else data.get("essences", data.get("messages", []))
    if not essences:
        return {"text": "暂无精华消息。"}
    lines = [f"精华消息共 {len(essences)} 条："]
    for e in essences[:20]:
        sender = e.get("sender_nick", "") or e.get("sender", {}).get("nickname", "")
        content = e.get("content", "") or str(e.get("message", "")) or ""
        msg_id = e.get("message_id", "")
        ts = e.get("time", "")
        if isinstance(content, list):
            parts = []
            for seg in content:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
            content = "".join(parts)
        content = str(content)[:80]
        lines.append(f"- [{msg_id}] {sender}: {content}" + (f" ({ts})" if ts else ""))
    if len(essences) > 20:
        lines.append("...（仅展示前20条）")
    return {"text": "\n".join(lines)}


async def _exec_get_shut_list(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    raw = await bot.api.call_action("get_group_shut_list", group_id=group_id)
    data = raw.get("data", raw)
    shut_members = data if isinstance(data, list) else data.get("shut_list", data.get("members", []))
    if not shut_members:
        return {"text": "当前没有成员被禁言。"}
    lines = [f"禁言列表共 {len(shut_members)} 人："]
    for m in shut_members[:50]:
        uid = m.get("user_id", 0)
        nickname = m.get("nickname", "") or str(uid)
        duration = m.get("duration", m.get("ban_time", 0))
        lines.append(f"- {nickname}({uid}) 剩余 {duration} 秒")
    return {"text": "\n".join(lines)}


async def _exec_set_group_card(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.call_action(
        "set_group_card", group_id=group_id,
        user_id=args["user_id"], card=args["card"],
    )


async def _exec_delete_msg(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.call_action("delete_msg", message_id=args["message_id"])


async def _exec_whole_ban(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    enable = args.get("enable", True)
    return await bot.api.call_action(
        "set_group_whole_ban", group_id=group_id, enable=enable,
    )


async def _exec_set_admin(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.call_action(
        "set_group_admin", group_id=group_id,
        user_id=args["user_id"], enable=args.get("enable", True),
    )


async def _exec_send_poke(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    params = {"user_id": args["user_id"], "target_id": args["user_id"]}
    if group_id is not None:
        params["group_id"] = group_id
    return await bot.api.call_action("group_poke", **params)


async def _exec_send_like(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.call_action(
        "send_like", user_id=args["user_id"], times=args.get("times", 1),
    )


async def _exec_ocr_image(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.call_action("ocr_image", image=args["image"])


async def _exec_voice_to_text(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return await bot.api.call_action("fetch_ptt_text", message_id=args["message_id"])


async def _exec_query_character(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    return _query_character(args["keyword"])


async def _exec_resolve_forward(args: dict, group_id: int | None, user_id: int | None, bot) -> dict:
    result_text = await _resolve_forward(str(args["forward_id"]), bot)
    return {"text": result_text}


# ---------------------------------------------------------------------------
# Tool catalog registration
# ---------------------------------------------------------------------------

_tool_defs: list[ToolDef] = []

_tool_defs.append(ToolDef(
    name="set_essence",
    description="设置或取消一条群精华消息",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "integer", "description": "目标消息ID"},
            "enabled": {"type": "boolean", "description": "true=设为精华，false=取消；默认true"},
        },
        "required": ["message_id"],
    },
    risk_level=RiskLevel.WRITE,
    category="management",
    executor=_exec_set_essence,
))
_tool_defs.append(ToolDef(
    name="react_emoji",
    description="添加或取消一条消息的表情反应/点赞",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "integer", "description": "要反应的消息ID"},
            "emoji_id": {"type": "integer", "description": "系统emoji的Unicode码点值，例如点赞=128077"},
            "enabled": {"type": "boolean", "description": "true=添加，false=取消；默认true"},
        },
        "required": ["message_id", "emoji_id"],
    },
    risk_level=RiskLevel.WRITE,
    category="interaction",
    executor=_exec_react_emoji,
))
_tool_defs.append(ToolDef(
    name="send_message",
    description="发送消息到当前群聊或私聊（用于需要单独通知的场景）",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要发送的消息内容"}},
        "required": ["text"],
    },
    risk_level=RiskLevel.WRITE,
    category="interaction",
    executor=_exec_send_message,
))
_tool_defs.append(ToolDef(
    name="mute_user",
    description="禁言群成员",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "要禁言的用户QQ号"},
            "duration": {"type": "integer", "description": "禁言时长(秒)，设为0表示解除禁言"},
        },
        "required": ["user_id", "duration"],
    },
    risk_level=RiskLevel.DESTRUCTIVE,
    category="management",
    executor=_exec_mute_user,
))
_tool_defs.append(ToolDef(
    name="kick_user",
    description="踢出群成员",
    parameters={
        "type": "object",
        "properties": {"user_id": {"type": "integer", "description": "要踢出的用户QQ号"}},
        "required": ["user_id"],
    },
    risk_level=RiskLevel.DESTRUCTIVE,
    category="management",
    executor=_exec_kick_user,
))
_tool_defs.append(ToolDef(
    name="get_current_time",
    description="获取当前日期时间 (YYYY-MM-DD HH:MM:SS)。查询时间段时先调用此工具。",
    parameters={"type": "object", "properties": {}, "required": []},
    risk_level=RiskLevel.READ_ONLY,
    category="query",
    executor=_exec_get_current_time,
    cache_ttl=5.0,
))
_tool_defs.append(ToolDef(
    name="query_history",
    description="查询聊天记录。需要了解上下文时主动调用。默认返回当前群聊最近30条消息（含所有成员及bot）。\n使用场景：\n- 了解最近在聊什么 → 直接调用，不传参数\n- 查询某个用户 → 传 user_id\n- 查询时间段 → 传 time_after + time_before 圈定范围\n- 搜索关键词 → 传 keyword\n- 只看bot自己的消息 → bot_scope=only\n- 查某条消息详情 → 传 message_id",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "integer", "description": "精确查找指定消息ID"},
            "keyword": {"type": "string", "description": "搜索关键词，模糊匹配消息内容"},
            "user_id": {"type": "integer", "description": "只查特定用户的消息"},
            "limit": {"type": "integer", "description": "返回条数，默认30，最多50"},
            "time_after": {"type": "string", "description": "开始时间 (YYYY-MM-DD HH:MM:SS)，配合 time_before 查询时间范围"},
            "time_before": {"type": "string", "description": "结束时间 (YYYY-MM-DD HH:MM:SS)，配合 time_after 查询时间范围"},
            "bot_scope": {"type": "string", "description": "include(默认)/exclude(排除bot)/only(仅bot)"},
        },
        "required": [],
    },
    risk_level=RiskLevel.READ_ONLY,
    category="query",
    executor=_exec_query_history,
))
_tool_defs.append(ToolDef(
    name="resolve_forward",
    description="解析合并转发消息的具体内容。当消息显示为 [合并转发 xxx] 时调用此工具获取其中的对话内容。支持嵌套转发。",
    parameters={
        "type": "object",
        "properties": {"forward_id": {"type": "string", "description": "合并转发ID，从消息内容中的 [合并转发 xxx] 获取"}},
        "required": ["forward_id"],
    },
    risk_level=RiskLevel.READ_ONLY,
    category="perception",
    executor=_exec_resolve_forward,
))
_tool_defs.append(ToolDef(
    name="get_group_info",
    description="获取群聊详细信息：群名、人数、创建时间等。用于了解群组基本情况。",
    parameters={"type": "object", "properties": {}, "required": []},
    risk_level=RiskLevel.READ_ONLY,
    category="query",
    executor=_exec_get_group_info,
))
_tool_defs.append(ToolDef(
    name="get_member_info",
    description="查询群成员的群名片、QQ号、角色(owner/admin/member)。需要了解某人身份时调用。",
    parameters={
        "type": "object",
        "properties": {"user_id": {"type": "integer", "description": "要查询的用户QQ号"}},
        "required": ["user_id"],
    },
    risk_level=RiskLevel.READ_ONLY,
    category="query",
    executor=_exec_get_member_info,
))
_tool_defs.append(ToolDef(
    name="get_member_list",
    description="获取群成员完整列表，了解群内有哪些人、谁在活跃。结果可能较大，会返回摘要。",
    parameters={"type": "object", "properties": {}, "required": []},
    risk_level=RiskLevel.READ_ONLY,
    category="query",
    executor=_exec_get_member_list,
))
_tool_defs.append(ToolDef(
    name="get_essence_list",
    description="获取群精华消息列表，了解群里哪些消息被标记为精华。",
    parameters={"type": "object", "properties": {}, "required": []},
    risk_level=RiskLevel.READ_ONLY,
    category="query",
    executor=_exec_get_essence_list,
))
_tool_defs.append(ToolDef(
    name="get_shut_list",
    description="获取当前群禁言列表，查看哪些成员被禁言及剩余时长。",
    parameters={"type": "object", "properties": {}, "required": []},
    risk_level=RiskLevel.READ_ONLY,
    category="query",
    executor=_exec_get_shut_list,
))
_tool_defs.append(ToolDef(
    name="set_group_card",
    description="修改群成员的群名片。需要修改某人的群昵称时调用。",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "要修改的用户QQ号"},
            "card": {"type": "string", "description": "新的群名片内容，空字符串表示清除群名片"},
        },
        "required": ["user_id", "card"],
    },
    risk_level=RiskLevel.DESTRUCTIVE,
    category="management",
    executor=_exec_set_group_card,
))
_tool_defs.append(ToolDef(
    name="delete_msg",
    description="撤回一条消息。注意：只能撤回bot自己发送的消息或bot是管理员时撤回他人消息。",
    parameters={
        "type": "object",
        "properties": {"message_id": {"type": "integer", "description": "要撤回的消息ID"}},
        "required": ["message_id"],
    },
    risk_level=RiskLevel.DESTRUCTIVE,
    category="management",
    executor=_exec_delete_msg,
))
_tool_defs.append(ToolDef(
    name="whole_ban",
    description="开启或关闭全员禁言。需要bot是群主。",
    parameters={
        "type": "object",
        "properties": {"enable": {"type": "boolean", "description": "true=开启全员禁言，false=关闭，默认true"}},
        "required": [],
    },
    risk_level=RiskLevel.DESTRUCTIVE,
    category="management",
    executor=_exec_whole_ban,
    requires_admin=True,
))
_tool_defs.append(ToolDef(
    name="set_admin",
    description="设置或取消群管理员。需要bot是群主。",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "要设置的用户QQ号"},
            "enable": {"type": "boolean", "description": "true=设为管理员，false=取消管理员，默认true"},
        },
        "required": ["user_id"],
    },
    risk_level=RiskLevel.DESTRUCTIVE,
    category="management",
    executor=_exec_set_admin,
    requires_admin=True,
))
_tool_defs.append(ToolDef(
    name="send_poke",
    description="在当前群聊或私聊中戳一戳用户，用于轻量互动或吸引注意。",
    parameters={
        "type": "object",
        "properties": {"user_id": {"type": "integer", "description": "要戳的用户QQ号"}},
        "required": ["user_id"],
    },
    risk_level=RiskLevel.WRITE,
    category="interaction",
    executor=_exec_send_poke,
))
_tool_defs.append(ToolDef(
    name="send_like",
    description="给用户点赞（私聊场景为主）。",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "要对其点赞的用户QQ号"},
            "times": {"type": "integer", "description": "点赞次数，默认1，建议不超过10"},
        },
        "required": ["user_id"],
    },
    risk_level=RiskLevel.WRITE,
    category="interaction",
    executor=_exec_send_like,
))
_tool_defs.append(ToolDef(
    name="ocr_image",
    description="OCR识别图片中的文字。当用户发送图片并要求识别或询问图片内容时调用。注：仅Windows端NapCat支持。",
    parameters={
        "type": "object",
        "properties": {"image": {"type": "string", "description": "图片路径、URL或Base64编码的图片数据"}},
        "required": ["image"],
    },
    risk_level=RiskLevel.READ_ONLY,
    category="perception",
    executor=_exec_ocr_image,
))
_tool_defs.append(ToolDef(
    name="voice_to_text",
    description="将语音消息转为文字。当用户发送语音需要了解说了什么时调用。",
    parameters={
        "type": "object",
        "properties": {"message_id": {"type": "integer", "description": "语音消息的消息ID"}},
        "required": ["message_id"],
    },
    risk_level=RiskLevel.READ_ONLY,
    category="perception",
    executor=_exec_voice_to_text,
))
_tool_defs.append(ToolDef(
    name="query_character",
    description="查询葬送的芙莉莲世界观的人物设定、关系、魔法、历史等背景知识。当你需要了解某个人物、事件、魔法或世界观细节时调用。",
    parameters={
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "查询关键词：人名(辛美尔/菲伦/海塔/艾泽/赛丽艾...)、章节名(核心准则/过去/魔法/年表...)、魔法名、事件名等"}},
        "required": ["keyword"],
    },
    risk_level=RiskLevel.READ_ONLY,
    category="cognition",
    executor=_exec_query_character,
))

# Backward-compatible TOOL_DEFS export
TOOL_DEFS: list[dict[str, Any]] = [tool.to_openai_schema() for tool in _tool_defs]


def register_llm_tools(catalog: ToolCatalog) -> None:
    """Register built-in definitions into one Bot catalog."""
    for tool in _tool_defs:
        catalog.register(tool)


# ---------------------------------------------------------------------------
# resolve_forward helper
# ---------------------------------------------------------------------------


async def _resolve_forward(forward_id: str, bot, depth: int = 0) -> str:
    """Recursively resolve merged-forward message content.

    Handles nested forwards up to *depth* 3, rendering text / image /
    at / face segments.  Returns a human-readable multi-line string.
    """
    if depth >= 3:
        return "[嵌套转发层数过深，已截断]"

    try:
        raw = await bot.api.get_forward_msg(forward_id)
        data = raw.get("data", raw)
        messages = data.get("messages", [])
        if not messages:
            return "[转发内容为空]"
    except Exception as e:
        logger.opt(exception=True).error(f"resolve_forward({forward_id}) failed: {e}")
        return f"[解析转发失败: {e}]"

    indent = "  " * (depth + 1)
    lines: list[str] = []
    for msg in messages:
        sender = msg.get("sender", {})
        nickname = sender.get("nickname", "") or str(sender.get("user_id", "?"))
        content = msg.get("message", "") or msg.get("raw_message", "")

        if isinstance(content, list):
            parts: list[str] = []
            for seg in content:
                if not isinstance(seg, dict):
                    parts.append(str(seg))
                    continue
                seg_type = seg.get("type", "")
                seg_data = seg.get("data", {}) or {}
                if seg_type == "text":
                    parts.append(seg_data.get("text", ""))
                elif seg_type == "image":
                    parts.append("[图片]")
                elif seg_type == "forward":
                    nested_id = seg_data.get("id", "")
                    if nested_id:
                        nested_text = await _resolve_forward(nested_id, bot, depth + 1)
                        parts.append(nested_text)
                    else:
                        parts.append("[合并转发]")
                elif seg_type == "at":
                    parts.append(f"@{seg_data.get('qq', '?')}")
                elif seg_type == "face":
                    parts.append("[表情]")
                elif seg_type == "reply":
                    parts.append("[回复]")
                else:
                    parts.append(f"[{seg_type}]")
            content = "".join(parts)
        elif isinstance(content, str):
            content = content or ""
        else:
            content = str(content)

        lines.append(f"{indent}{nickname}: {content}")

    label = "[合并转发]" if depth == 0 else "[嵌套转发]"
    return f"{label}\n" + "\n".join(lines)


# Character lore query (query_character tool)
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

# Module-level cache
_CHARACTER_SECTIONS: dict[str, str] | None = None
_CHARACTER_FULL_TEXT: str | None = None


def _init_character_doc() -> tuple[dict[str, str], str]:
    """Parse frieren.md into (sections, full_text). Cached after first load."""
    global _CHARACTER_SECTIONS, _CHARACTER_FULL_TEXT

    if _CHARACTER_SECTIONS is not None:
        return _CHARACTER_SECTIONS, _CHARACTER_FULL_TEXT

    import re

    # src/core/llm/tools/providers/qq.py -> project_root/config/frieren.md
    doc_path = Path(__file__).resolve().parents[5] / "config" / "frieren.md"

    if not doc_path.exists():
        logger.warning(f"Character doc not found: {doc_path}")
        _CHARACTER_SECTIONS = {}
        _CHARACTER_FULL_TEXT = ""
        return _CHARACTER_SECTIONS, _CHARACTER_FULL_TEXT

    text = doc_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    _CHARACTER_FULL_TEXT = text

    # -- Parse sections by #/## headers --
    sections: dict[str, str] = {}
    current_title = "_preamble"
    current_lines: list[str] = []

    for line in text.split("\n"):
        m = re.match(r"^#{1,2}\s+(.+)$", line)
        if m:
            if current_lines:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = m.group(1).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections[current_title] = "\n".join(current_lines).strip()

    _CHARACTER_SECTIONS = sections
    logger.info(f"Character doc loaded: {len(sections)} sections, {len(text)} chars from {doc_path}")
    return sections, text


def _query_character(keyword: str) -> dict:
    """Search frieren.md by keyword. Returns matched section or context window."""
    sections, full_text = _init_character_doc()
    if not sections:
        return {"text": "人物设定文档未找到，请联系管理员配置 frieren.md。"}

    kw = keyword.strip()

    # Phase 1: section title match (e.g. "魔法介绍", "年表", "人物关系")
    for title, content in sections.items():
        if kw in title:
            # If matched section is small, extend to include subsequent content
            # up to the next level-1 header
            if content.count("\n") < 3:
                idx = full_text.find(content)
                if idx >= 0:
                    rest = full_text[idx + len(content):]
                    next_h1 = rest.find("\n# ")
                    if next_h1 >= 0:
                        content = full_text[idx:idx + len(content) + next_h1]
                    else:
                        content = full_text[idx:]
            return {"text": _truncate_content(content)}

    # Phase 2: full-text search with context window
    # Prefer standalone-line matches (character name on its own line)
    idx = -1
    lines = full_text.split("\n")
    char_pos = 0
    for line in lines:
        if line.strip() == kw:
            idx = char_pos
            break
        char_pos += len(line) + 1  # +1 for \n

    # Fall back to first occurrence anywhere
    if idx == -1:
        idx = full_text.find(kw)

    if idx == -1:
        return {"text": f"未找到与 {kw!r} 相关的设定内容。试试查询人名（如辛美尔、菲伦、海塔、赛丽艾）或章节（如魔法、年表、核心准则）。"}

    # Count occurrences
    count = full_text.count(kw)

    # Extract context around the match: ~300 chars before, ~1200 after
    ctx_start = max(0, idx - 300)
    ctx_end = min(len(full_text), idx + len(kw) + 1200)
    snippet = full_text[ctx_start:ctx_end].strip()

    if count > 1:
        snippet += f"\n\n（全文共 {count} 处匹配，以上为第一处。可用更具体的关键词精确查询）"

    return {"text": _truncate_content(snippet)}


def _truncate_content(text: str, limit: int = 1500) -> str:
    """Truncate text to ~limit chars, preserving line boundaries."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + "\n\n...(内容较长已截断，可缩小查询范围或查询子章节)"
