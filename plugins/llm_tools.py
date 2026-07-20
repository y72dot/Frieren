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
            "description": "获取所有可用工具的参数说明和使用示例。不确定某个工具怎么用或参数含义时调用此工具。也可用 tool_name=\"chain_guide\" 查看工具链式调用指南，用 tool_name=\"decision_guide\" 查看常见任务的工具组合建议。",
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
    # ── 方向一: 信息获取工具 ──
    {
        "type": "function",
        "function": {
            "name": "get_group_info",
            "description": "获取群聊详细信息：群名、人数、创建时间等。用于了解群组基本情况。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_member_info",
            "description": "查询群成员的群名片、QQ号、角色(owner/admin/member)。需要了解某人身份时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "要查询的用户QQ号",
                    }
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_member_list",
            "description": "获取群成员完整列表，了解群内有哪些人、谁在活跃。结果可能较大，会返回摘要。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_essence_list",
            "description": "获取群精华消息列表，了解群里哪些消息被标记为精华。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shut_list",
            "description": "获取当前群禁言列表，查看哪些成员被禁言及剩余时长。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ── 方向二: 群管理工具 ──
    {
        "type": "function",
        "function": {
            "name": "set_group_card",
            "description": "修改群成员的群名片。需要修改某人的群昵称时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "要修改的用户QQ号",
                    },
                    "card": {
                        "type": "string",
                        "description": "新的群名片内容，空字符串表示清除群名片",
                    },
                },
                "required": ["user_id", "card"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_msg",
            "description": "撤回一条消息。注意：只能撤回bot自己发送的消息或bot是管理员时撤回他人消息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "要撤回的消息ID",
                    }
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "whole_ban",
            "description": "开启或关闭全员禁言。需要bot是群主。",
            "parameters": {
                "type": "object",
                "properties": {
                    "enable": {
                        "type": "boolean",
                        "description": "true=开启全员禁言，false=关闭",
                    }
                },
                "required": ["enable"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_admin",
            "description": "设置或取消群管理员。需要bot是群主。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "要设置的用户QQ号",
                    },
                    "enable": {
                        "type": "boolean",
                        "description": "true=设为管理员，false=取消管理员",
                    },
                },
                "required": ["user_id", "enable"],
            },
        },
    },
    # ── 方向三: 互动与内容感知 ──
    {
        "type": "function",
        "function": {
            "name": "send_poke",
            "description": "戳一戳群成员，用于轻量互动或吸引注意。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "要戳的用户QQ号",
                    }
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_like",
            "description": "给用户点赞（私聊场景为主）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "要对其点赞的用户QQ号",
                    },
                    "times": {
                        "type": "integer",
                        "description": "点赞次数，默认1，建议不超过10",
                    },
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ocr_image",
            "description": "OCR识别图片中的文字。当用户发送图片并要求识别或询问图片内容时调用。注：仅Windows端NapCat支持。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "图片路径、URL或Base64编码的图片数据",
                    }
                },
                "required": ["image"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "voice_to_text",
            "description": "将语音消息转为文字。当用户发送语音需要了解说了什么时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "语音消息的消息ID",
                    }
                },
                "required": ["message_id"],
            },
        },
    },
    # ── 方向四: Agent 认知增强 ──
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "在执行复杂操作前梳理思路。常用于需要多步推理的场景：分析问题→收集信息→决策→执行。详细的工具链式调用指南请见 tool_help(tool_name=\"chain_guide\")。思考内容仅自己可见。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "思考过程，包括分析步骤、需要哪些信息、预期结果",
                    }
                },
                "required": ["reasoning"],
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
    # ── 方向一: 信息获取工具 ──
    if name == "get_group_info":
        result = await bot.api.get_group_info(group_id)
        data = result.get("data", result)
        # Extract key fields for compact output
        info_fields = {}
        for k in ("group_name", "group_id", "member_count", "max_member_count", "create_time", "group_level", "owner_id", "admin_count", "group_memo"):
            if k in data:
                info_fields[k] = data[k]
        if not info_fields:
            info_fields = data
        return {"text": json.dumps(info_fields, ensure_ascii=False, indent=2)}
    if name == "get_member_info":
        result = await bot.api.get_group_member_info(group_id, args["user_id"])
        data = result.get("data", result)
        # Keep key identity fields
        fields = {}
        for k in ("user_id", "nickname", "card", "role", "title", "join_time", "last_speak_time", "level"):
            if k in data:
                fields[k] = data[k]
        if not fields:
            fields = data
        return {"text": json.dumps(fields, ensure_ascii=False, indent=2)}
    if name == "get_member_list":
        result = await bot.api.get_group_member_list(group_id)
        data = result.get("data", result)
        members = data if isinstance(data, list) else data.get("members", [])
        total = len(members)
        # Role labels
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
    if name == "get_essence_list":
        raw = await bot.api.call_action("get_essence_msg_list", group_id=group_id)
        data = raw.get("data", raw)
        essences = data if isinstance(data, list) else data.get("essences", data.get("messages", []))
        if not essences:
            return {"text": "暂无精华消息。"}
        # Show up to 20
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
            lines.append(f"...（仅展示前20条）")
        return {"text": "\n".join(lines)}
    if name == "get_shut_list":
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
    # ── 方向二: 群管理工具 ──
    if name == "set_group_card":
        return await bot.api.call_action(
            "set_group_card", group_id=group_id,
            user_id=args["user_id"], card=args["card"],
        )
    if name == "delete_msg":
        return await bot.api.call_action("delete_msg", message_id=args["message_id"])
    if name == "whole_ban":
        enable = args.get("enable", True)
        return await bot.api.call_action(
            "set_group_whole_ban", group_id=group_id, enable=enable,
        )
    if name == "set_admin":
        return await bot.api.call_action(
            "set_group_admin", group_id=group_id,
            user_id=args["user_id"], enable=args.get("enable", True),
        )
    # ── 方向三: 互动与内容感知 ──
    if name == "send_poke":
        return await bot.api.send_group_poke(group_id, args["user_id"])
    if name == "send_like":
        return await bot.api.call_action(
            "send_like", user_id=args["user_id"], times=args.get("times", 1),
        )
    if name == "ocr_image":
        return await bot.api.call_action("ocr_image", image=args["image"])
    if name == "voice_to_text":
        return await bot.api.call_action("fetch_ptt_text", message_id=args["message_id"])
    # ── 方向四: Agent 认知增强 ──
    if name == "think":
        logger.info(f"THINK tool: {args.get('reasoning', '')}")
        return {"acknowledged": True}
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
    # ── 方向一: 信息获取工具 ──
    "get_group_info": {
        "desc": "获取群聊详细信息（群名、人数、创建时间等）",
        "params": [],
        "example": "get_group_info() — 返回当前群的基本信息",
    },
    "get_member_info": {
        "desc": "查询群成员的群名片、QQ、角色",
        "params": [
            ("user_id", "integer", "是", "要查询的用户QQ号"),
        ],
        "example": "get_member_info(user_id=123456) — 查询该用户的群身份信息",
    },
    "get_member_list": {
        "desc": "获取群成员完整列表（返回摘要，最多显示前100人）",
        "params": [],
        "example": "get_member_list() — 列出群成员概况",
    },
    "get_essence_list": {
        "desc": "获取群精华消息列表",
        "params": [],
        "example": "get_essence_list() — 查看群里的精华消息",
    },
    "get_shut_list": {
        "desc": "获取当前被禁言的成员列表",
        "params": [],
        "example": "get_shut_list() — 查看谁被禁言了",
    },
    # ── 方向二: 群管理工具 ──
    "set_group_card": {
        "desc": "修改群成员群名片",
        "params": [
            ("user_id", "integer", "是", "目标用户QQ号"),
            ("card", "string", "是", "新群名片内容，空字符串=清除"),
        ],
        "example": "set_group_card(user_id=123456, card=\"新昵称\") — 修改某人的群名片",
    },
    "delete_msg": {
        "desc": "撤回消息（需bot是管理员才能撤回他人消息）",
        "params": [
            ("message_id", "integer", "是", "要撤回的消息ID"),
        ],
        "example": "delete_msg(message_id=12345) — 撤回该消息",
    },
    "whole_ban": {
        "desc": "全员禁言/解禁（需bot是群主）",
        "params": [
            ("enable", "boolean", "是", "true=开启全员禁言，false=关闭"),
        ],
        "example": "whole_ban(enable=true) — 开启全员禁言",
    },
    "set_admin": {
        "desc": "设置/取消管理员（需bot是群主）",
        "params": [
            ("user_id", "integer", "是", "目标用户QQ号"),
            ("enable", "boolean", "是", "true=设为管理，false=取消管理"),
        ],
        "example": "set_admin(user_id=123456, enable=true) — 将该用户设为管理员",
    },
    # ── 方向三: 互动与内容感知 ──
    "send_poke": {
        "desc": "戳一戳群成员",
        "params": [
            ("user_id", "integer", "是", "要戳的用户QQ号"),
        ],
        "example": "send_poke(user_id=123456) — 戳一下这个用户",
    },
    "send_like": {
        "desc": "给用户点赞（私聊场景）",
        "params": [
            ("user_id", "integer", "是", "要对其点赞的用户QQ号"),
            ("times", "integer", "否", "点赞次数，默认1，不超过10"),
        ],
        "example": "send_like(user_id=123456, times=3) — 给该用户点3个赞",
    },
    "ocr_image": {
        "desc": "OCR识别图片中的文字（仅Windows端NapCat支持）",
        "params": [
            ("image", "string", "是", "图片路径、URL或Base64"),
        ],
        "example": "ocr_image(image=\"http://example.com/img.png\") — 识别图片文字",
    },
    "voice_to_text": {
        "desc": "语音转文字",
        "params": [
            ("message_id", "integer", "是", "语音消息的消息ID"),
        ],
        "example": "voice_to_text(message_id=12345) — 将语音消息转为文字",
    },
    # ── 方向四: Agent 认知增强 ──
    "think": {
        "desc": "梳理思路，规划复杂操作步骤。结果仅自己可见。",
        "params": [
            ("reasoning", "string", "是", "思考内容：分析问题、需要哪些信息、分几步执行"),
        ],
        "example": "think(reasoning=\"我需要找出谁发了广告。1. 查询最近消息中的广告关键词 2. 找出违规用户 3. 按规则处理\") — 多步推理前先思考",
    },
}


# ---------------------------------------------------------------------------
# Non-tool guide texts (accessible via tool_help)
# ---------------------------------------------------------------------------

_GUIDE_TEXTS = {
    "chain_guide": {
        "desc": "工具链式调用指南",
        "content": (
            "复杂操作按「分析→收集信息→决策→执行」流程：\n"
            "- 需要多步推理时，先调用 think(reasoning=\"...\") 梳理步骤\n"
            "- 不了解群组状况时，先调用查询工具获取上下文（如 get_member_list + get_essence_list + get_shut_list）\n"
            "- 不知道对方身份时，先调用 get_member_info 确认角色\n"
            "- 需要证据时，先调用 query_history 搜索相关消息，再执行操作\n"
            "- 操作完成后可视情况用 send_message 通知结果"
        ),
    },
    "decision_guide": {
        "desc": "常见任务工具组合建议",
        "content": (
            "- 群状况概览 → get_group_info + get_member_list + get_essence_list\n"
            "- 查某人 → get_member_info(user_id) + query_history(user_id)\n"
            "- 处理违规 → think → query_history(关键词) → mute_user / kick_user / delete_msg\n"
            "- 精华操作 → set_essence / remove_essence，需提供消息ID\n"
            "- 改名片 → set_group_card(user_id, card)\n"
            "- 语音/图片 → voice_to_text / ocr_image 获取内容后再回答"
        ),
    },
}


def _help_all() -> dict:
    lines = ["可用工具一览：\n"]
    lines.append("【查询】get_current_time / query_history / get_group_info / get_member_info / get_member_list / get_essence_list / get_shut_list")
    lines.append("【管理】set_essence / remove_essence / mute_user / kick_user / set_group_card / delete_msg / whole_ban / set_admin")
    lines.append("【互动】send_message / react_emoji(点赞128077,笑哭128514,心10084) / send_poke / send_like")
    lines.append("【感知】ocr_image(仅Windows) / voice_to_text / resolve_forward")
    lines.append("【辅助】think / tool_help")
    lines.append(f"\n共 {len(_HELP_TEXTS)} 个工具。")
    lines.append("查看某工具详情请用 tool_help(tool_name=\"xxx\")。")
    lines.append("查看工具链式调用指南请用 tool_help(tool_name=\"chain_guide\")。")
    lines.append("查看常见任务决策指南请用 tool_help(tool_name=\"decision_guide\")。")
    return {"text": "\n".join(lines)}


def _help_single(tool_name: str) -> dict:
    guide = _GUIDE_TEXTS.get(tool_name)
    if guide:
        return {"text": f"**{tool_name}** — {guide['desc']}\n\n{guide['content']}"}

    info = _HELP_TEXTS.get(tool_name)
    if not info:
        return {"text": f"未找到工具 {tool_name!r}。可用工具：{', '.join(_HELP_TEXTS)}。可用指南：{', '.join(_GUIDE_TEXTS)}"}
    lines = [f"**{tool_name}** — {info['desc']}\n"]
    if info["params"]:
        lines.append("参数：")
        for pname, ptype, preq, pdesc in info["params"]:
            lines.append(f"  - {pname} ({ptype}, {preq}): {pdesc}")
    else:
        lines.append("参数：无")
    lines.append(f"\n用例：{info['example']}")
    return {"text": "\n".join(lines)}
