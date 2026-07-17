"""Tests for the repeater plugin – covers all boundary conditions."""

from __future__ import annotations

import asyncio

from src.plugin.base import Event

from plugins.repeater import RepeaterPlugin, _group_history


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

    # --- basic happy path ---

    def test_two_different_users_triggers_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "world")

        assert plugin.match(event_a) is True
        assert asyncio.run(plugin.handle(event_a, bot)) is False
        assert bot.api.calls == []

        assert plugin.match(event_b) is True
        assert asyncio.run(plugin.handle(event_b, bot)) is False
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "world"}
        ]

    # --- boundary: bot's own message ---

    def test_bot_own_message_ignored(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_bot = _make_group_event(bot.config.bot.qq, "world")

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
        event_b = _make_group_event(111, "world")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))
        # Same user, should not repeat
        assert bot.api.calls == []

    # --- boundary: different groups isolated ---

    def test_different_groups_isolated(self, bot):
        plugin = RepeaterPlugin()
        event_g1 = _make_group_event(111, "hello", group_id=100)
        event_g2 = _make_group_event(222, "world", group_id=200)

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
        event_a = _make_group_event(111, "A")
        event_b = _make_group_event(222, "B")
        event_c = _make_group_event(333, "C")
        event_d = _make_group_event(444, "D")

        asyncio.run(plugin.handle(event_a, bot))  # history: [A]
        assert bot.api.calls == []

        asyncio.run(plugin.handle(event_b, bot))  # history: [A,B], diff → repeat B, clear
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "B"}
        ]

        asyncio.run(plugin.handle(event_c, bot))  # history: [C] only, no repeat
        assert len(bot.api.calls) == 1

        asyncio.run(plugin.handle(event_d, bot))  # history: [C,D], diff → repeat D
        assert len(bot.api.calls) == 2
        assert bot.api.calls[1] == {
            "method": "send_group_msg", "group_id": 456, "message": "D"
        }

    # --- behavior: history cleared after repeat ---

    def test_history_cleared_after_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "world")

        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))  # triggers repeat, clears history
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "world"}
        ]
        assert _group_history.get(456, []) == []

    # --- scenario: two independent rounds (A,B → repeat, C,D → repeat) ---

    def test_two_independent_rounds(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "A")
        event_b = _make_group_event(222, "B")
        event_c = _make_group_event(333, "C")
        event_d = _make_group_event(444, "D")

        # Round 1: A then B triggers repeat
        asyncio.run(plugin.handle(event_a, bot))
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "B"}
        ]

        # Round 2: C then D triggers repeat independently
        asyncio.run(plugin.handle(event_c, bot))
        asyncio.run(plugin.handle(event_d, bot))
        assert len(bot.api.calls) == 2
        assert bot.api.calls[1] == {
            "method": "send_group_msg", "group_id": 456, "message": "D"
        }

    # --- scenario: A, A, B pattern ---

    def test_aab_pattern(self, bot):
        plugin = RepeaterPlugin()
        event_a1 = _make_group_event(111, "A1")
        event_a2 = _make_group_event(111, "A2")
        event_b = _make_group_event(222, "B")

        asyncio.run(plugin.handle(event_a1, bot))  # history: [A1]
        asyncio.run(plugin.handle(event_a2, bot))  # history: [A1,A2], same user → no repeat
        assert bot.api.calls == []

        asyncio.run(plugin.handle(event_b, bot))  # history: [A2,B], diff → repeat B
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "B"}
        ]

    # --- behavior: handle() always returns False ---

    def test_repeat_does_not_consume_event(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "world")

        assert asyncio.run(plugin.handle(event_a, bot)) is False
        assert asyncio.run(plugin.handle(event_b, bot)) is False
        # Repeat happened but event was not consumed
        assert len(bot.api.calls) == 1
