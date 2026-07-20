"""Tests for llm_sender – message chunking and sending."""

from __future__ import annotations

import pytest

from plugins.llm_sender import _QQ_MSG_LIMIT, _split_message


class TestSplitMessage:
    def test_short_message_no_split(self):
        result = _split_message("hello", _QQ_MSG_LIMIT)
        assert result == ["hello"]

    def test_exact_limit_no_split(self):
        text = "x" * _QQ_MSG_LIMIT
        result = _split_message(text, _QQ_MSG_LIMIT)
        assert result == [text]

    def test_split_on_newline(self):
        # Newline is well within the second half, so split there
        line1 = "a" * 3000
        line2 = "b" * 2000
        text = line1 + "\n" + line2
        result = _split_message(text, _QQ_MSG_LIMIT)
        assert len(result) == 2
        assert result[0] == line1  # split at newline
        assert result[1] == line2

    def test_split_no_newline_fallback(self):
        text = "x" * (_QQ_MSG_LIMIT + 100)
        result = _split_message(text, _QQ_MSG_LIMIT)
        assert len(result) == 2
        assert len(result[0]) == _QQ_MSG_LIMIT
        assert len(result[1]) == 100

    def test_multiple_splits(self):
        text = "x" * (_QQ_MSG_LIMIT * 3 + 50)
        result = _split_message(text, _QQ_MSG_LIMIT)
        assert len(result) == 4
        assert sum(len(c) for c in result) == len(text)

    def test_prefer_newline_over_hard_split(self):
        # If there's a newline in the second half, it should split there
        line1 = "a" * 3000
        line2 = "b" * 1000
        text = line1 + "\n" + line2 + "c" * 2000
        result = _split_message(text, _QQ_MSG_LIMIT)
        # First chunk: up to the newline
        assert line1 in result[0]
        # The rest is in subsequent chunks
        assert len(result) >= 2

    def test_empty_string(self):
        result = _split_message("", 1000)
        assert result == [""]


@pytest.mark.asyncio
async def test_sender_handler_no_match():
    """Handler returns False for non-send llm_type payloads."""
    from plugins.llm_sender import llm_sender_handler

    result = await llm_sender_handler({"llm_type": "other"}, None)
    assert result is False


@pytest.mark.asyncio
async def test_sender_dispatches_group_message(bot):
    """Sender sends group messages and records them to msg_store."""
    from plugins.llm_sender import llm_sender_handler

    result = await llm_sender_handler(
        {
            "llm_type": "send",
            "target_id": 12345,
            "is_group": True,
            "text": "hello world",
        },
        bot,
    )
    assert result is False
    assert len(bot.api.calls) == 1
    assert bot.api.calls[0]["method"] == "send_group_msg"
    assert bot.api.calls[0]["group_id"] == 12345
    assert bot.api.calls[0]["message"] == "hello world"

    # Verify bot message was recorded to msg_store
    msgs = bot.msg_store.recent(12345)
    assert len(msgs) == 1
    assert msgs[0].user_id == bot.config.bot.qq
    assert msgs[0].nickname == "test"
    assert msgs[0].content == "hello world"
    assert msgs[0].group_id == 12345


@pytest.mark.asyncio
async def test_sender_dispatches_private_message(bot):
    """Sender sends private messages and records them to msg_store."""
    from plugins.llm_sender import llm_sender_handler

    result = await llm_sender_handler(
        {
            "llm_type": "send",
            "target_id": 999,
            "is_group": False,
            "text": "private hi",
        },
        bot,
    )
    assert result is False
    assert len(bot.api.calls) == 1
    assert bot.api.calls[0]["method"] == "send_private_msg"
    assert bot.api.calls[0]["user_id"] == 999
    assert bot.api.calls[0]["message"] == "private hi"

    # Verify bot message was recorded to msg_store
    msgs = bot.msg_store.recent_private(bot.config.bot.qq)
    assert len(msgs) == 1
    assert msgs[0].content == "private hi"
    assert msgs[0].group_id is None


@pytest.mark.asyncio
async def test_sender_chunks_long_message(bot):
    """Long messages are split into multiple sends, each recorded."""
    from plugins.llm_sender import llm_sender_handler

    long_text = "A" * (_QQ_MSG_LIMIT + 500)
    result = await llm_sender_handler(
        {
            "llm_type": "send",
            "target_id": 12345,
            "is_group": True,
            "text": long_text,
        },
        bot,
    )
    assert result is False
    assert len(bot.api.calls) == 2

    # Both chunks recorded in msg_store
    msgs = bot.msg_store.recent(12345)
    assert len(msgs) == 2
