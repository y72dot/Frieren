"""LLM tools plugin: registers and executes function calling tools for the LLM agent."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.core.message_bus import MessageType
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
            "description": "查询聊天记录。需要了解上下文时主动调用。所有参数可组合（AND语义）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，模糊匹配消息内容"},
                    "user_id": {"type": "integer", "description": "只查特定用户的消息"},
                    "limit": {"type": "integer", "description": "返回条数，默认10，最多50"},
                    "time_after": {"type": "string", "description": "YYYY-MM-DD HH:MM:SS 格式，只返回此时间之后的消息"},
                    "time_before": {"type": "string", "description": "YYYY-MM-DD HH:MM:SS 格式，只返回此时间之前的消息"},
                    "bot_scope": {"type": "string", "description": "exclude(默认排除bot)/include(含bot)/only(仅bot)"},
                },
                "required": [],
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
            dt = _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            delta = dt - _dt.datetime(1970, 1, 1)
            return int(delta.total_seconds() - _UTC_OFFSET)

        keyword = args.get("keyword")
        uid = args.get("user_id")
        limit = min(args.get("limit", 10), 50) if args.get("limit") is not None else 10
        time_after = args.get("time_after")
        time_before = args.get("time_before")
        bot_scope = args.get("bot_scope", "exclude")

        kwargs: dict = dict(n=limit)
        if group_id:
            kwargs["group_id"] = group_id
            kwargs["is_group"] = True
        else:
            kwargs["is_group"] = False

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

        if not msgs:
            return {"text": "没有找到相关消息。"}

        from plugins.llm_memory import _format_msg, _clean_content

        lines = [_format_msg(m, bot.config.bot.qq, include_time=True) for m in msgs if _clean_content(m.content)]
        return {"text": "找到以下消息：\n" + "\n".join(lines)}
    return {"error": f"unknown tool: {name}"}
