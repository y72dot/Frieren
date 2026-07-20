"""LLM memory plugin: helper functions for formatting chat history."""

from __future__ import annotations

import datetime
import re

_CQ_REPLY = re.compile(r"\[CQ:reply,id=(\d+)\]")
_CQ_AT = re.compile(r"\[CQ:at,qq=(\d+)\]")
_CQ_IMAGE = re.compile(r"\[CQ:image,[^\]]*\]")
_CQ_FORWARD = re.compile(r"\[CQ:forward,id=([^\]]+)\]")
_CQ_CLEANUP = re.compile(r"\[CQ:[^\]]+\]")


def _clean_content(content: str) -> str:
    """Convert CQ codes to human-readable text, strip remaining CQ codes."""
    text = _CQ_REPLY.sub(r"回复[\1] ", content)
    text = _CQ_AT.sub(r"@\1", text)
    text = _CQ_IMAGE.sub("[图片]", text)
    text = _CQ_FORWARD.sub(r"[合并转发 \1]", text)
    text = _CQ_CLEANUP.sub("", text)
    return text.strip()


def _format_msg(m, bot_qq: int | None = None, include_time: bool = False) -> str:
    """Format a stored message as '[message_id] MM-DD HH:MM nickname(user_id): content'."""
    clean = _clean_content(m.content)
    name = m.nickname or str(m.user_id)
    tag = " [自己]" if bot_qq and m.user_id == bot_qq else ""
    if include_time:
        ts = datetime.datetime.fromtimestamp(m.time).strftime("%m-%d %H:%M")
        return f"[{m.message_id}] {ts} {name}({m.user_id}){tag}: {clean}"
    return f"[{m.message_id}] {name}({m.user_id}){tag}: {clean}"
