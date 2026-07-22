"""Shared formatting helpers for LLM chat context and history tools."""

from __future__ import annotations

import datetime


def clean_content(content: str) -> str:
    """Return trimmed content while preserving CQ codes verbatim."""
    return content.strip()


def format_message(message, bot_qq: int | None = None, include_time: bool = False) -> str:
    """Format one stored message for an LLM context window."""
    clean = clean_content(message.content)
    name = message.nickname or str(message.user_id)
    tag = " [自己]" if bot_qq and message.user_id == bot_qq else ""
    if include_time:
        timestamp = datetime.datetime.fromtimestamp(message.time).strftime("%m-%d %H:%M")
        return (
            f"[{message.message_id}] {timestamp} "
            f"{name}({message.user_id}){tag}: {clean}"
        )
    return f"[{message.message_id}] {name}({message.user_id}){tag}: {clean}"
