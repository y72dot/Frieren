"""LLM tools plugin: registers and executes function calling tools for the LLM agent."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.core.message_bus import MessageType
from src.core.message_store import StoredMessage
from src.plugin.decorators import subscribe

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function calling format)
# ---------------------------------------------------------------------------

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "set_essence",
            "description": "将一条消息设为群精华消息",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "要设精的消息ID",
                    }
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_essence",
            "description": "取消一条群精华消息",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "要取消精华的消息ID",
                    }
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "react_emoji",
            "description": "对一条消息进行表情反应/点赞",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "要反应的消息ID",
                    },
                    "emoji_id": {
                        "type": "integer",
                        "description": "系统emoji的Unicode码点值，例如点赞=128077",
                    },
                },
                "required": ["message_id", "emoji_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "发送消息到当前群聊或私聊（用于需要单独通知的场景）",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要发送的消息内容",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mute_user",
            "description": "禁言群成员",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "要禁言的用户QQ号",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "禁言时长(秒)，设为0表示解除禁言",
                    },
                },
                "required": ["user_id", "duration"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kick_user",
            "description": "踢出群成员",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "要踢出的用户QQ号",
                    }
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前日期时间 (YYYY-MM-DD HH:MM:SS)。查询时间段时先调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_history",
            "description": "查询聊天记录。需要了解上下文时主动调用。默认返回当前群聊最近30条消息（含所有成员及bot）。\n使用场景：\n- 了解最近在聊什么 → 直接调用，不传参数\n- 查询某个用户 → 传 user_id\n- 查询时间段 → 传 time_after + time_before 圈定范围\n- 搜索关键词 → 传 keyword\n- 只看bot自己的消息 → bot_scope=only\n- 查某条消息详情 → 传 message_id",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_help",
            "description": "获取所有可用工具的参数说明和使用示例。不确定某个工具怎么用或参数含义时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "可选，指定工具名查看单个工具详情。不传则列出全部工具概览。",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_forward",
            "description": "解析合并转发消息的具体内容。当消息显示为 [合并转发 xxx] 时调用此工具获取其中的对话内容。支持嵌套转发。",
            "parameters": {
                "type": "object",
                "properties": {
                    "forward_id": {
                        "type": "string",
                        "description": "合并转发ID，从消息内容中的 [合并转发 xxx] 获取",
                    }
                },
                "required": ["forward_id"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


@subscribe(MessageType.INTERNAL, priority=30)
async def llm_tools_handler(payload: dict[str, Any], bot) -> bool:
    """Handle ``llm_type: "tool"`` INTERNAL messages – execute LLM tool calls."""
    if payload.get("llm_type") != "tool":
        return False

    tool_calls: list = payload["tool_calls"]
    response_buf: dict = payload["response_buffer"]
    group_id: int | None = payload.get("group_id")
    user_id: int | None = payload.get("user_id")

    results: list[dict] = []
    for tc in tool_calls:
        # Handle both ToolCall objects (from provider) and dict format (from raw API)
        if hasattr(tc, "name"):
            call_id = tc.id
            name = tc.name
            args = tc.arguments
        else:
            fn = tc.get("function", {})
            call_id = tc.get("id", "")
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        try:
            result = await _execute(name, args, group_id, user_id, bot)
        except Exception as e:
            logger.opt(exception=True).error(f"Tool '{name}' execution failed: {e}")
            result = {"error": str(e)}
        results.append({"call_id": call_id, "name": name, "result": result})

    response_buf["results"] = results
    return False


# ---------------------------------------------------------------------------
# Tool execution mapping
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


async def _execute(
    name: str,
    args: dict,
    group_id: int | None,
    user_id: int | None,
    bot,
) -> dict:
    if name == "set_essence":
        return await bot.api.set_essence_msg(args["message_id"])
    if name == "remove_essence":
        return await bot.api.delete_essence_msg(args["message_id"])
    if name == "react_emoji":
        return await bot.api.call_action(
            "set_msg_emoji_like",
            message_id=args["message_id"],
            emoji_id=args["emoji_id"],
            set=True,
        )
    if name == "send_message":
        if group_id:
            await bot.api.send_group_msg(group_id, args["text"])
        else:
            await bot.api.send_private_msg(user_id, args["text"])
        return {"sent": True}
    if name == "mute_user":
        return await bot.api.set_group_ban(group_id, args["user_id"], args["duration"])
    if name == "kick_user":
        return await bot.api.set_group_kick(group_id, args["user_id"])
    if name == "get_current_time":
        import datetime
        return {"datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if name == "query_history":
        import datetime as _dt
        import time as _time

        _UTC_OFFSET = -_time.timezone if not _time.daylight else -_time.altzone

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
            return int(delta.total_seconds() - _UTC_OFFSET)

        msg_id = args.get("message_id")
        keyword = args.get("keyword")
        uid = args.get("user_id")
        limit = min(args.get("limit", 30), 50)
        time_after = args.get("time_after")
        time_before = args.get("time_before")
        bot_scope = args.get("bot_scope", "include")

        kwargs: dict = dict(n=limit)
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

        msgs = bot.msg_store.query(**kwargs)

        has_other_filters = bool(keyword or uid is not None or time_after is not None or time_before is not None)
        if not msgs and msg_id is not None and not has_other_filters:
            try:
                raw = await bot.api.get_msg(msg_id)
                data = raw.get("data", raw)
                sender = data.get("sender", {})
                _uid = sender.get("user_id", 0)
                _nick = sender.get("nickname", "") or sender.get("card", "")
                _content = data.get("message", data.get("raw_message", "")) or json.dumps(data, ensure_ascii=False)
                _time = data.get("time", 0)
                msgs = [StoredMessage(
                    message_id=msg_id, user_id=_uid, nickname=_nick,
                    content=str(_content), time=_time, group_id=group_id,
                )]
            except Exception:
                pass

        if not msgs:
            return {"text": "没有找到相关消息。"}

        from plugins.llm_memory import _format_msg

        lines = [_format_msg(m, bot.config.bot.qq, include_time=True) for m in msgs if m.content.strip()]
        return {"text": "找到以下消息：\n" + "\n".join(lines)}
    if name == "tool_help":
        tool_name = args.get("tool_name")
        if tool_name:
            return _help_single(tool_name)
        return _help_all()
    if name == "resolve_forward":
        result_text = await _resolve_forward(str(args["forward_id"]), bot)
        return {"text": result_text}
    return {"error": f"unknown tool: {name}"}


# ---------------------------------------------------------------------------
# tool_help helpers
# ---------------------------------------------------------------------------


_HELP_TEXTS = {
    "set_essence": {
        "desc": "将一条消息设为群精华消息",
        "params": [
            ("message_id", "integer", "是", "要设精的消息ID"),
        ],
        "example": 'set_essence(message_id=12345) — 将消息12345设为精华',
    },
    "remove_essence": {
        "desc": "取消一条群精华消息",
        "params": [
            ("message_id", "integer", "是", "要取消精华的消息ID"),
        ],
        "example": 'remove_essence(message_id=12345) — 取消消息12345的精华',
    },
    "react_emoji": {
        "desc": "对一条消息进行表情反应/点赞",
        "params": [
            ("message_id", "integer", "是", "要反应的消息ID"),
            ("emoji_id", "integer", "是", "系统emoji的Unicode码点值，点赞=128077"),
        ],
        "example": "react_emoji(message_id=12345, emoji_id=128077) — 给消息12345点赞",
    },
    "send_message": {
        "desc": "发送消息到当前群聊或私聊",
        "params": [
            ("text", "string", "是", "要发送的消息内容"),
        ],
        "example": "send_message(text=\"同学们早上好\") — 发送一条消息",
    },
    "mute_user": {
        "desc": "禁言群成员",
        "params": [
            ("user_id", "integer", "是", "要禁言的用户QQ号"),
            ("duration", "integer", "是", "禁言时长(秒)，0=解除禁言"),
        ],
        "example": "mute_user(user_id=123456, duration=600) — 禁言用户10分钟",
    },
    "kick_user": {
        "desc": "踢出群成员",
        "params": [
            ("user_id", "integer", "是", "要踢出的用户QQ号"),
        ],
        "example": "kick_user(user_id=123456) — 将用户踢出群",
    },
    "get_current_time": {
        "desc": "获取当前日期时间 (YYYY-MM-DD HH:MM:SS)",
        "params": [],
        "example": "get_current_time() — 返回当前时间，查询时间段前应先调用",
    },
    "query_history": {
        "desc": "查询聊天记录，默认返回当前群最近30条消息（含bot）",
        "params": [
            ("message_id", "integer", "否", "精确查找指定消息ID"),
            ("keyword", "string", "否", "搜索关键词，模糊匹配消息内容"),
            ("user_id", "integer", "否", "只查特定用户的消息"),
            ("limit", "integer", "否", "返回条数，默认30，最多50"),
            ("time_after", "string", "否", "开始时间，格式 YYYY-MM-DD HH:MM:SS"),
            ("time_before", "string", "否", "结束时间，格式 YYYY-MM-DD HH:MM:SS"),
            ("bot_scope", "string", "否", "include(默认)/exclude(排除bot)/only(仅bot)"),
        ],
        "example": "query_history() — 查最近消息\nquery_history(keyword=\"作业\") — 搜索含「作业」的消息\nquery_history(time_after=\"2026-07-20 10:00:00\", time_before=\"2026-07-20 15:00:00\") — 查时间范围",
    },
    "tool_help": {
        "desc": "获取所有可用工具的参数说明和使用示例",
        "params": [
            ("tool_name", "string", "否", "指定工具名查看详情，不传列出全部概览"),
        ],
        "example": "tool_help() — 列出所有工具\ntool_help(tool_name=\"query_history\") — 查看query_history详情",
    },
    "resolve_forward": {
        "desc": "解析合并转发消息内容，支持嵌套转发",
        "params": [
            ("forward_id", "string", "是", "合并转发ID，从消息中的 [合并转发 xxx] 获取"),
        ],
        "example": "resolve_forward(forward_id=\"abc123\") — 解析该转发消息的具体对话内容",
    },
}


def _help_all() -> dict:
    lines = ["可用工具一览：\n"]
    for i, (name, info) in enumerate(_HELP_TEXTS.items(), 1):
        lines.append(f"{i}. {name} — {info['desc']}")
    lines.append(f"\n共 {len(_HELP_TEXTS)} 个工具。查看某工具详情请用 tool_help(tool_name=\"xxx\")。")
    return {"text": "\n".join(lines)}


def _help_single(tool_name: str) -> dict:
    info = _HELP_TEXTS.get(tool_name)
    if not info:
        return {"text": f"未找到工具 {tool_name!r}。可用工具：{', '.join(_HELP_TEXTS)}"}
    lines = [f"**{tool_name}** — {info['desc']}\n"]
    if info["params"]:
        lines.append("参数：")
        for pname, ptype, preq, pdesc in info["params"]:
            lines.append(f"  - {pname} ({ptype}, {preq}): {pdesc}")
    else:
        lines.append("参数：无")
    lines.append(f"\n用例：{info['example']}")
    return {"text": "\n".join(lines)}
