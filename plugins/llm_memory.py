"""LLM memory plugin: helper functions for formatting chat history."""

from __future__ import annotations

import datetime


def _clean_content(content: str) -> str:
    """Return trimmed content. CQ codes are kept as-is for the LLM."""
    return content.strip()


def _format_msg(m, bot_qq: int | None = None, include_time: bool = False) -> str:
    """Format a stored message as '[message_id] MM-DD HH:MM nickname(user_id): content'."""
    clean = _clean_content(m.content)
    name = m.nickname or str(m.user_id)
    tag = " [自己]" if bot_qq and m.user_id == bot_qq else ""
    if include_time:
        ts = datetime.datetime.fromtimestamp(m.time).strftime("%m-%d %H:%M")
        return f"[{m.message_id}] {ts} {name}({m.user_id}){tag}: {clean}"
    return f"[{m.message_id}] {name}({m.user_id}){tag}: {clean}"
