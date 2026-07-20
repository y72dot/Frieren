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
            "name": "query_history",
            "description": "查询聊天记录。需要了解上下文时主动调用。可按关键词搜索、按用户筛选、或获取最近消息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，不传则返回最近消息"},
                    "user_id": {"type": "integer", "description": "只查特定用户的消息"},
                    "limit": {"type": "integer", "description": "返回条数，默认10，最多30"},
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
    if name == "query_history":
        keyword = args.get("keyword")
        uid = args.get("user_id")
        limit = min(args.get("limit", 10), 30)

        if keyword and group_id:
            msgs = bot.msg_store.search(group_id, keyword, n=limit)
        elif uid and group_id:
            msgs = bot.msg_store.by_user(group_id, uid, n=limit)
        elif group_id:
            msgs = bot.msg_store.recent(group_id, n=limit, exclude_user_id=bot.config.bot.qq)
        else:
            msgs = bot.msg_store.recent_private(user_id, n=limit)

        if not msgs:
            return {"text": "没有找到相关消息。"}

        from plugins.llm_memory import _format_msg, _clean_content

        lines = [_format_msg(m, bot.config.bot.qq) for m in msgs if _clean_content(m.content)]
        return {"text": "找到以下消息：\n" + "\n".join(lines)}
    return {"error": f"unknown tool: {name}"}
