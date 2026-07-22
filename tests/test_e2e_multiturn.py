"""E2E multi-turn scenario tests: complex LLM interaction chains."""

from __future__ import annotations

import time

import pytest

from src.core.llm import LlmResponse, ToolCall
from src.core.llm.content_guard import SAFE_FINAL_FALLBACK
from tests.conftest_e2e import (
    FakeLlmProvider,
    assert_api_called,
    dispatch_raw_event,
    e2e_bot,  # noqa: F401
    e2e_llm_bot,  # noqa: F401
)


def _make_provider(bot, responses: list[LlmResponse]) -> FakeLlmProvider:
    provider = FakeLlmProvider()
    provider.responses = responses
    bot.llm_provider = provider
    return provider


def _raw_at_msg(user_id=111, text="Hello", group_id=456, msg_id=1) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "user_id": user_id,
        "group_id": group_id,
        "raw_message": f"[CQ:at,qq=123456] {text}",
        "message_id": msg_id,
        "time": int(time.time()),
        "sender": {"nickname": "Alice", "card": ""},
    }


class TestMultiTurnScenarios:
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_query_action_chain(self, e2e_llm_bot):
        """query_history → mute_user → final reply chain."""
        # Seed message store for query_history
        now = int(time.time())
        e2e_llm_bot.msg_store.record_bot_message(
            message_id=100, group_id=456, user_id=999,
            nickname="Spammer", content="Buy my product!!!",
            time=now, is_group=True,
        )

        _make_provider(
            e2e_llm_bot,
            [
                LlmResponse(
                    tool_calls=[
                        ToolCall(
                            id="c1", name="query_history",
                            arguments={"keyword": "product"},
                        )
                    ]
                ),
                LlmResponse(
                    tool_calls=[
                        ToolCall(
                            id="c2", name="mute_user",
                            arguments={"user_id": 999, "duration": 600},
                        )
                    ]
                ),
                LlmResponse(text="Spammer has been muted for 10 minutes."),
            ],
        )

        raw = _raw_at_msg(text="Check for spam")
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert_api_called(
            e2e_llm_bot, "send_group_msg",
            group_id=456, message="Spammer has been muted for 10 minutes.",
        )
        # Verify mute was called
        assert any(
            c.get("method") == "set_group_ban"
            and c.get("user_id") == 999
            and c.get("duration") == 600
            for c in e2e_llm_bot.api.calls
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_max_turns_exceeded(self, e2e_llm_bot):
        """max_turns=3, continuous tool calls → forced final text reply."""
        # Configure max_turns=3
        e2e_llm_bot.config.llm.max_turns = 3

        # Return tool calls for all 3 turns (exhaust max_turns)
        _make_provider(
            e2e_llm_bot,
            [
                LlmResponse(tool_calls=[ToolCall(id="c1", name="get_current_time", arguments={})]),
                LlmResponse(tool_calls=[ToolCall(id="c2", name="query_history", arguments={"limit": 1})]),
                LlmResponse(tool_calls=[ToolCall(id="c3", name="get_current_time", arguments={})]),
                # 4th call: forced final (no tools)
                LlmResponse(text="Forced final reply."),
            ],
        )

        raw = _raw_at_msg()
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert_api_called(
            e2e_llm_bot, "send_group_msg",
            group_id=456, message="Forced final reply.",
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_max_turns_blocks_dsml_forced_final(self, e2e_llm_bot):
        e2e_llm_bot.config.llm.max_turns = 1
        _make_provider(
            e2e_llm_bot,
            [
                LlmResponse(
                    tool_calls=[
                        ToolCall(id="c1", name="get_current_time", arguments={})
                    ]
                ),
                LlmResponse(
                    text=(
                        '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke '
                        'name="web_fetch">'
                    )
                ),
            ],
        )

        await dispatch_raw_event(e2e_llm_bot, _raw_at_msg())

        assert_api_called(
            e2e_llm_bot,
            "send_group_msg",
            group_id=456,
            message=SAFE_FINAL_FALLBACK,
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_session_summary_uses_agent_tool_count(
        self, e2e_llm_bot, monkeypatch
    ):
        import plugins.llm_core as llm_core

        class CapturingSessionLogger:
            last = None

            def __init__(self, session_key):
                self.tool_count = 0
                self.ended_tool_count = None
                CapturingSessionLogger.last = self

            def tool_calls_result(self, calls):
                self.tool_count += len(calls)

            def session_end(self, turns):
                self.ended_tool_count = self.tool_count

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        monkeypatch.setattr(llm_core, "LlmSessionLogger", CapturingSessionLogger)
        _make_provider(
            e2e_llm_bot,
            [
                LlmResponse(
                    tool_calls=[
                        ToolCall(id="c1", name="get_current_time", arguments={})
                    ]
                ),
                LlmResponse(text="done"),
            ],
        )

        await dispatch_raw_event(e2e_llm_bot, _raw_at_msg())

        assert CapturingSessionLogger.last.ended_tool_count == 1

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_session_ttl_expiry(self, e2e_llm_bot):
        """Pre-set expired session → new trigger creates NEW session."""
        from src.core.llm.session_manager import Session

        # Manually set an expired session
        old_time = time.time() - 99999  # far in the past (TTL=3600)
        old_session = Session(
            session_key="group:456",
            messages=[{"role": "system", "content": "old"}, {"role": "user", "content": "old msg"}],
            created_at=old_time,
            last_active=old_time,
        )
        e2e_llm_bot.session_mgr._cache["group:456"] = old_session

        _make_provider(e2e_llm_bot, [LlmResponse(text="Fresh session reply.")])

        raw = _raw_at_msg(text="New message")
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert_api_called(
            e2e_llm_bot, "send_group_msg",
            group_id=456, message="Fresh session reply.",
        )
        # Session should be updated with fresh timestamp, and it's a NEW session
        # (old session was expired so old msg should NOT be present)
        # New session now has: system + auto_init tool_call + auto_init result + user
        session = e2e_llm_bot.session_mgr._cache["group:456"]
        messages = session.messages
        # User message is at index 3 (after system + auto_init tool_call + tool result)
        assert len(messages) == 4
        assert messages[1]["role"] == "assistant"  # synthetic tool_call
        assert messages[3]["role"] == "user"
        assert "New message" in messages[3]["content"]

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_session_across_messages(self, e2e_llm_bot):
        """Two consecutive messages append to the same session."""
        _make_provider(
            e2e_llm_bot,
            [LlmResponse(text="Reply 1"), LlmResponse(text="Reply 2")],
        )

        # First message
        raw1 = _raw_at_msg(msg_id=1, text="Message 1")
        await dispatch_raw_event(e2e_llm_bot, raw1)

        # Second message (within TTL)
        raw2 = _raw_at_msg(msg_id=2, text="Message 2")
        await dispatch_raw_event(e2e_llm_bot, raw2)

        # Both should generate replies
        calls = e2e_llm_bot.llm_provider.calls
        assert len(calls) == 2

        # Second call should have accumulated messages
        msgs2 = calls[1]["messages"]
        # Should include system + user1 + assistant1 + user2
        user_contents = [m["content"] for m in msgs2 if m["role"] == "user"]
        assert len(user_contents) >= 2

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_member_list_then_query(self, e2e_llm_bot):
        """get_member_list → extract user → query_history(user_id) chain."""
        # Set up member list response
        e2e_llm_bot.api.set_response(
            "get_group_member_list",
            {
                "data": [
                    {"user_id": 1, "nickname": "Owner", "card": "", "role": "owner"},
                    {"user_id": 555, "nickname": "Target", "card": "", "role": "member"},
                ]
            },
        )

        _make_provider(
            e2e_llm_bot,
            [
                LlmResponse(
                    tool_calls=[ToolCall(id="c1", name="get_member_list", arguments={})]
                ),
                LlmResponse(
                    tool_calls=[
                        ToolCall(
                            id="c2", name="query_history",
                            arguments={"user_id": 555},
                        )
                    ]
                ),
                LlmResponse(text="Found target user's messages."),
            ],
        )

        raw = _raw_at_msg(text="Check members")
        await dispatch_raw_event(e2e_llm_bot, raw)

        assert_api_called(
            e2e_llm_bot, "send_group_msg",
            group_id=456, message="Found target user's messages.",
        )

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_tool_error_propagation(self, e2e_llm_bot):
        """A tool that fails returns error → conversation continues."""
        _make_provider(
            e2e_llm_bot,
            [
                LlmResponse(
                    tool_calls=[
                        # mute_user without user_id will KeyError
                        ToolCall(id="c1", name="mute_user", arguments={"duration": 60}),
                    ]
                ),
                LlmResponse(text="Tool failed but I recovered."),
            ],
        )

        raw = _raw_at_msg()
        await dispatch_raw_event(e2e_llm_bot, raw)

        # Error should not crash the chain; LLM continues and sends reply
        assert_api_called(
            e2e_llm_bot, "send_group_msg",
            group_id=456, message="Tool failed but I recovered.",
        )
