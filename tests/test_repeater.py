"""Tests for the repeater plugin – covers all boundary conditions."""

from __future__ import annotations

import asyncio

from src.plugin.base import Event

from plugins.repeater import RepeaterPlugin, _group_history, _last_repeated, _locks


def _make_group_event(user_id: int, message: str, group_id: int = 456) -> Event:
    return Event(
        type="message.group",
        user_id=user_id,
        message=message,
        group_id=group_id,
        is_group=True,
    )


class TestRepeater:
    def setup_method(self):
        _group_history.clear()
        _last_repeated.clear()
        _locks.clear()

    # --- basic happy path ---

    def test_two_different_users_triggers_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        assert plugin.match(event_a) is True
        assert asyncio.run(plugin.handle(event_a, bot)) is False
        assert bot.api.calls == []

        assert plugin.match(event_b) is True
        assert asyncio.run(plugin.handle(event_b, bot)) is False
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "hello"}
        ]

    # --- boundary: different content → no repeat ---

    def test_different_content_no_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "world")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))
        # Different content, should NOT repeat
        assert bot.api.calls == []

    # --- boundary: bot's own message ---

    def test_bot_own_message_ignored(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_bot = _make_group_event(bot.config.bot.qq, "hello")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_bot, bot))
        # Bot's message is skipped, so history still has only 1 entry → no repeat
        assert bot.api.calls == []

    # --- boundary: first message (history < 2) ---

    def test_first_message_no_repeat(self, bot):
        plugin = RepeaterPlugin()
        event = _make_group_event(111, "hello")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- boundary: same user consecutive messages ---

    def test_same_user_consecutive_no_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(111, "hello")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))
        # Same user, should not repeat
        assert bot.api.calls == []

    # --- boundary: different groups isolated ---

    def test_different_groups_isolated(self, bot):
        plugin = RepeaterPlugin()
        event_g1 = _make_group_event(111, "hello", group_id=100)
        event_g2 = _make_group_event(222, "hello", group_id=200)

        asyncio.run(plugin.handle(event_g1, bot))
        asyncio.run(plugin.handle(event_g2, bot))
        # Each group has only 1 message → no repeat
        assert bot.api.calls == []

    # --- boundary: empty message ---

    def test_empty_message_ignored(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_empty = _make_group_event(222, "   ")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_empty, bot))
        # Empty message not recorded, history still has 1 entry → no repeat
        assert bot.api.calls == []

    # --- scenario: fast alternating (A, B, C, D) ---

    def test_fast_alternating_users(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "X")
        event_b = _make_group_event(222, "X")
        event_c = _make_group_event(333, "Y")
        event_d = _make_group_event(444, "Y")

        asyncio.run(plugin.handle(event_a, bot))  # history: [X]
        assert bot.api.calls == []

        asyncio.run(plugin.handle(event_b, bot))  # history: [X,X], diff user + same → repeat X, clear
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "X"}
        ]

        asyncio.run(plugin.handle(event_c, bot))  # history: [Y] only, no repeat
        assert len(bot.api.calls) == 1

        asyncio.run(plugin.handle(event_d, bot))  # history: [Y,Y], diff user + same → repeat Y
        assert len(bot.api.calls) == 2
        assert bot.api.calls[1] == {
            "method": "send_group_msg", "group_id": 456, "message": "Y"
        }

    # --- behavior: history cleared after repeat ---

    def test_history_cleared_after_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))  # triggers repeat, clears history
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "hello"}
        ]
        assert _group_history.get(456, []) == []

    # --- scenario: two independent rounds (A,B → repeat, C,D → repeat) ---

    def test_two_independent_rounds(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "X")
        event_b = _make_group_event(222, "X")
        event_c = _make_group_event(333, "Y")
        event_d = _make_group_event(444, "Y")

        # Round 1: A then B (same content "X") triggers repeat
        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "X"}
        ]

        # Round 2: C then D (same content "Y") triggers repeat independently
        asyncio.run(plugin.handle(event_c, bot))
        asyncio.run(plugin.handle(event_d, bot))
        assert len(bot.api.calls) == 2
        assert bot.api.calls[1] == {
            "method": "send_group_msg", "group_id": 456, "message": "Y"
        }

    # --- scenario: A, A, B pattern ---

    def test_aab_pattern(self, bot):
        plugin = RepeaterPlugin()
        event_a1 = _make_group_event(111, "X")
        event_a2 = _make_group_event(111, "Y")
        event_b = _make_group_event(222, "Y")

        asyncio.run(plugin.handle(event_a1, bot))  # history: [X]
        asyncio.run(plugin.handle(event_a2, bot))  # history: [X,Y], same user → no repeat
        assert bot.api.calls == []

        asyncio.run(plugin.handle(event_b, bot))  # history: [Y,Y], diff user + same content → repeat Y
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "Y"}
        ]

    # --- behavior: handle() always returns False ---

    def test_repeat_does_not_consume_event(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        assert asyncio.run(plugin.handle(event_a, bot)) is False
        assert asyncio.run(plugin.handle(event_b, bot)) is False
        # Repeat happened but event was not consumed
        assert len(bot.api.calls) == 1

    # --- same message not repeated twice ---

    def test_same_message_not_repeated_twice(self, bot):
        plugin = RepeaterPlugin()

        # Round 1: A and B both say "hello" → bot repeats "hello"
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "hello"}
        ]

        # Round 2: C and D both say "hello" again → should NOT repeat
        event_c = _make_group_event(333, "hello")
        event_d = _make_group_event(444, "hello")

        asyncio.run(plugin.handle(event_c, bot))
        asyncio.run(plugin.handle(event_d, bot))
        # Still only 1 call, second "hello" repeat was suppressed
        assert len(bot.api.calls) == 1

    def test_different_message_repeated_after_same_one(self, bot):
        plugin = RepeaterPlugin()

        # Round 1: "hello" gets repeated
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "hello"}
        ]

        # Round 2: same "hello" → skipped
        event_c = _make_group_event(333, "hello")
        event_d = _make_group_event(444, "hello")

        asyncio.run(plugin.handle(event_c, bot))
        asyncio.run(plugin.handle(event_d, bot))
        assert len(bot.api.calls) == 1  # still only round 1

        # Round 3: different content "world" → should repeat normally
        event_e = _make_group_event(555, "world")
        event_f = _make_group_event(666, "world")

        asyncio.run(plugin.handle(event_e, bot))
        asyncio.run(plugin.handle(event_f, bot))
        assert len(bot.api.calls) == 2
        assert bot.api.calls[1] == {
            "method": "send_group_msg", "group_id": 456, "message": "world"
        }

    # --- reply messages: same text, different reply targets → no repeat ---

    def test_reply_same_text_diff_target_no_repeat(self, bot):
        plugin = RepeaterPlugin()
        # Two replies with same text but different reply IDs (quoting different messages)
        event_a = _make_group_event(111, "[CQ:reply,id=1000]hello")
        event_b = _make_group_event(222, "[CQ:reply,id=2000]hello")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))
        # Different raw_message (reply IDs differ) → no repeat
        assert bot.api.calls == []
