"""Tests for llm_memory – chat history injection into LLM context."""

from __future__ import annotations

import pytest

from src.core.llm.session import SessionManager
from src.core.message_store import StoredMessage


class TestLlmMemoryHandler:
    @pytest.fixture
    def mock_session_mgr(self, monkeypatch):
        """Patch llm_core._session_mgr with a test instance."""
        sm = SessionManager(max_messages=20)
        monkeypatch.setattr("plugins.llm_core._session_mgr", sm)
        return sm

    @pytest.mark.asyncio
    async def test_no_match(self, bot):
        """Returns False for non-context llm_type payloads."""
        from plugins.llm_memory import llm_memory_handler

        result = await llm_memory_handler({"llm_type": "other"}, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_session_mgr_none_returns_false(self, bot, monkeypatch):
        """Returns False when _session_mgr is None."""
        monkeypatch.setattr("plugins.llm_core._session_mgr", None)
        from plugins.llm_memory import llm_memory_handler

        result = await llm_memory_handler(
            {"llm_type": "context", "session_key": "group:123", "is_group": True},
            bot,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_no_recent_messages(self, bot, mock_session_mgr, monkeypatch):
        """No-op when msg_store has no recent messages."""
        monkeypatch.setattr(bot.msg_store, "recent", lambda *a, **kw: [])

        from plugins.llm_memory import llm_memory_handler

        result = await llm_memory_handler(
            {"llm_type": "context", "session_key": "group:123", "is_group": True},
            bot,
        )
        assert result is False
        msgs = await mock_session_mgr.get_messages("group:123")
        assert msgs == []  # No context injected

    @pytest.mark.asyncio
    async def test_injects_group_history(self, bot, mock_session_mgr, monkeypatch):
        """Recent group messages are injected as context."""
        fake_msgs = [
            StoredMessage(
                message_id=1,
                user_id=100,
                nickname="Alice",
                content="hello",
                time=1000,
                group_id=123,
            ),
            StoredMessage(
                message_id=2,
                user_id=200,
                nickname="Bob",
                content="world",
                time=1001,
                group_id=123,
            ),
        ]
        monkeypatch.setattr(bot.msg_store, "recent", lambda *a, **kw: fake_msgs)

        from plugins.llm_memory import llm_memory_handler

        result = await llm_memory_handler(
            {"llm_type": "context", "session_key": "group:123", "is_group": True},
            bot,
        )
        assert result is False
        msgs = await mock_session_mgr.get_messages("group:123")
        assert len(msgs) == 1  # One system context message
        assert msgs[0]["role"] == "system"
        assert "[最近聊天记录]" in msgs[0]["content"]
        assert "Alice: hello" in msgs[0]["content"]
        assert "Bob: world" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_injects_private_history(self, bot, mock_session_mgr, monkeypatch):
        """Recent private messages are injected as context."""
        fake_msgs = [
            StoredMessage(
                message_id=1,
                user_id=999,
                nickname="User",
                content="private hi",
                time=1000,
                group_id=None,
            ),
        ]
        monkeypatch.setattr(
            bot.msg_store, "recent_private", lambda *a, **kw: fake_msgs
        )

        from plugins.llm_memory import llm_memory_handler

        result = await llm_memory_handler(
            {
                "llm_type": "context",
                "session_key": "private:999",
                "is_group": False,
            },
            bot,
        )
        assert result is False
        msgs = await mock_session_mgr.get_messages("private:999")
        assert len(msgs) == 1
        assert "private hi" in msgs[0]["content"]
