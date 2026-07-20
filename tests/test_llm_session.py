"""Tests for SessionManager – conversation buffer with context injection."""

from __future__ import annotations

import pytest

from src.core.llm.session import SessionManager


class TestSessionManager:
    @pytest.fixture
    def sm(self):
        return SessionManager(max_messages=10)

    @pytest.mark.asyncio
    async def test_add_and_get_messages(self, sm):
        await sm.add_message("s1", "user", "hello")
        await sm.add_message("s1", "assistant", "hi there")

        msgs = await sm.get_messages("s1")
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "hello"}
        assert msgs[1] == {"role": "assistant", "content": "hi there"}

    @pytest.mark.asyncio
    async def test_empty_session(self, sm):
        msgs = await sm.get_messages("nonexistent")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_add_context(self, sm):
        await sm.add_message("s1", "user", "hi")
        await sm.add_context("s1", "history", "Alice: hey\nBob: yo")

        msgs = await sm.get_messages("s1")
        assert len(msgs) == 2
        # Context is rendered as a system message
        assert msgs[0] == {"role": "system", "content": "Alice: hey\nBob: yo"}
        assert msgs[1] == {"role": "user", "content": "hi"}

    @pytest.mark.asyncio
    async def test_add_context_replaces_same_type(self, sm):
        await sm.add_context("s1", "history", "old context")
        await sm.add_context("s1", "history", "new context")

        msgs = await sm.get_messages("s1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "new context"

    @pytest.mark.asyncio
    async def test_multiple_context_types(self, sm):
        await sm.add_context("s1", "history", "recent chat")
        await sm.add_context("s1", "rules", "server rules here")

        msgs = await sm.get_messages("s1")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "recent chat"
        assert msgs[1]["content"] == "server rules here"

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self, sm):
        await sm.add_message("s1", "user", "msg1")
        await sm.add_message("s2", "user", "msg2")

        msgs1 = await sm.get_messages("s1")
        msgs2 = await sm.get_messages("s2")
        assert len(msgs1) == 1
        assert len(msgs2) == 1
        assert msgs1[0]["content"] == "msg1"
        assert msgs2[0]["content"] == "msg2"

    @pytest.mark.asyncio
    async def test_clear_session(self, sm):
        await sm.add_message("s1", "user", "hello")
        await sm.add_context("s1", "history", "ctx")

        sm.clear("s1")
        msgs = await sm.get_messages("s1")
        # Context persists after clear
        assert len(msgs) == 1
        assert msgs[0]["content"] == "ctx"

    @pytest.mark.asyncio
    async def test_trim_old_messages(self, sm):
        sm = SessionManager(max_messages=3)
        for i in range(5):
            await sm.add_message("s1", "user", f"msg{i}")

        msgs = await sm.get_messages("s1")
        assert len(msgs) == 3
        # Oldest messages trimmed
        assert msgs[0]["content"] == "msg2"
        assert msgs[2]["content"] == "msg4"
