"""Tests for the repeater plugin – covers all boundary conditions."""

from __future__ import annotations

import asyncio
import itertools

from src.plugin.base import Event

from plugins.repeater import RepeaterPlugin, _last_repeated, _locks

# Per-module counter for unique message_ids
_msg_id_counter = itertools.count(1)
_time_base = 1700000000


def _make_group_event(user_id: int, message: str, group_id: int = 456) -> Event:
    msg_id = next(_msg_id_counter)
    return Event(
        type="message.group",
        user_id=user_id,
        message_id=msg_id,
        message=message,
        group_id=group_id,
        is_group=True,
        raw={
            "message_id": msg_id,
            "user_id": user_id,
            "group_id": group_id,
            "raw_message": message,
            "time": _time_base + msg_id,
            "sender": {"nickname": str(user_id), "card": ""},
        },
    )


class TestRepeater:
    def setup_method(self):
        global _msg_id_counter
        _msg_id_counter = itertools.count(1)
        _last_repeated.clear()
        _locks.clear()

    # --- basic happy path ---

    def test_two_different_users_triggers_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        assert plugin.match(event_a) is True
        bot.msg_store.record(event_a)
        assert asyncio.run(plugin.handle(event_a, bot)) is False
        assert bot.api.calls == []

        bot.msg_store.record(event_b)
        assert plugin.match(event_b) is True
        assert asyncio.run(plugin.handle(event_b, bot)) is False
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "hello"}
        ]

    # --- boundary: different content -> no repeat ---

    def test_different_content_no_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "world")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == []

    # --- boundary: bot's own message ---

    def test_bot_own_message_ignored(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_bot = _make_group_event(bot.config.bot.qq, "hello")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_bot)
        asyncio.run(plugin.handle(event_bot, bot))
        # Bot's message is skipped, only 1 non-bot msg in store -> no repeat
        assert bot.api.calls == []

    # --- boundary: first message (history < 2) ---

    def test_first_message_no_repeat(self, bot):
        plugin = RepeaterPlugin()
        event = _make_group_event(111, "hello")

        bot.msg_store.record(event)
        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- boundary: same user consecutive messages ---

    def test_same_user_consecutive_no_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(111, "hello")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == []

    # --- boundary: different groups isolated ---

    def test_different_groups_isolated(self, bot):
        plugin = RepeaterPlugin()
        event_g1 = _make_group_event(111, "hello", group_id=100)
        event_g2 = _make_group_event(222, "hello", group_id=200)

        bot.msg_store.record(event_g1)
        asyncio.run(plugin.handle(event_g1, bot))
        bot.msg_store.record(event_g2)
        asyncio.run(plugin.handle(event_g2, bot))
        # Each group has only 1 non-bot message -> no repeat
        assert bot.api.calls == []

    # --- boundary: empty message ---

    def test_empty_message_ignored(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_empty = _make_group_event(222, "   ")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_empty)
        asyncio.run(plugin.handle(event_empty, bot))
        # Empty message skipped in handle(), only 1 non-bot non-empty msg -> no repeat
        # (event_empty IS in msg_store but doesn't trigger false repeat because
        #  handle returns early for empty messages)
        assert bot.api.calls == []

    # --- scenario: fast alternating (A, B, C, D) ---

    def test_fast_alternating_users(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "X")
        event_b = _make_group_event(222, "X")
        event_c = _make_group_event(333, "Y")
        event_d = _make_group_event(444, "Y")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        assert bot.api.calls == []

        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))  # diff user + same -> repeat X
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "X"}
        ]

        bot.msg_store.record(event_c)
        asyncio.run(plugin.handle(event_c, bot))  # only 1 non-bot msg of "Y" (event_b is "X")
        assert len(bot.api.calls) == 1

        bot.msg_store.record(event_d)
        asyncio.run(plugin.handle(event_d, bot))  # diff user + same -> repeat Y
        assert len(bot.api.calls) == 2
        assert bot.api.calls[1] == {
            "method": "send_group_msg", "group_id": 456, "message": "Y"
        }

    # --- behavior: history resolved via msg_store query ---

    def test_history_cleared_after_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))  # triggers repeat
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "hello"}
        ]
        # After repeat, _last_repeated tracks "hello" so a new round won't re-trigger
        # (verified by test_same_message_not_repeated_twice)

    # --- scenario: two independent rounds (A,B -> repeat, C,D -> repeat) ---

    def test_two_independent_rounds(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "X")
        event_b = _make_group_event(222, "X")
        event_c = _make_group_event(333, "Y")
        event_d = _make_group_event(444, "Y")

        # Round 1: A then B (same content "X") triggers repeat
        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "X"}
        ]

        # Round 2: C then D (same content "Y") triggers repeat independently
        bot.msg_store.record(event_c)
        asyncio.run(plugin.handle(event_c, bot))
        bot.msg_store.record(event_d)
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

        bot.msg_store.record(event_a1)
        asyncio.run(plugin.handle(event_a1, bot))
        bot.msg_store.record(event_a2)
        asyncio.run(plugin.handle(event_a2, bot))
        assert bot.api.calls == []

        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))  # diff user + same content -> repeat Y
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "Y"}
        ]

    # --- behavior: handle() always returns False ---

    def test_repeat_does_not_consume_event(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        bot.msg_store.record(event_a)
        assert asyncio.run(plugin.handle(event_a, bot)) is False
        bot.msg_store.record(event_b)
        assert asyncio.run(plugin.handle(event_b, bot)) is False
        assert len(bot.api.calls) == 1

    # --- same message not repeated twice ---

    def test_same_message_not_repeated_twice(self, bot):
        plugin = RepeaterPlugin()

        # Round 1: A and B both say "hello" -> bot repeats "hello"
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "hello"}
        ]

        # Round 2: C and D both say "hello" again -> should NOT repeat
        event_c = _make_group_event(333, "hello")
        event_d = _make_group_event(444, "hello")

        bot.msg_store.record(event_c)
        asyncio.run(plugin.handle(event_c, bot))
        bot.msg_store.record(event_d)
        asyncio.run(plugin.handle(event_d, bot))
        # Still only 1 call, second "hello" repeat was suppressed
        assert len(bot.api.calls) == 1

    def test_different_message_repeated_after_same_one(self, bot):
        plugin = RepeaterPlugin()

        # Round 1: "hello" gets repeated
        event_a = _make_group_event(111, "hello")
        event_b = _make_group_event(222, "hello")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))
        assert bot.api.calls == [
            {"method": "send_group_msg", "group_id": 456, "message": "hello"}
        ]

        # Round 2: same "hello" -> skipped
        event_c = _make_group_event(333, "hello")
        event_d = _make_group_event(444, "hello")

        bot.msg_store.record(event_c)
        asyncio.run(plugin.handle(event_c, bot))
        bot.msg_store.record(event_d)
        asyncio.run(plugin.handle(event_d, bot))
        assert len(bot.api.calls) == 1

        # Round 3: different content "world" -> should repeat normally
        event_e = _make_group_event(555, "world")
        event_f = _make_group_event(666, "world")

        bot.msg_store.record(event_e)
        asyncio.run(plugin.handle(event_e, bot))
        bot.msg_store.record(event_f)
        asyncio.run(plugin.handle(event_f, bot))
        assert len(bot.api.calls) == 2
        assert bot.api.calls[1] == {
            "method": "send_group_msg", "group_id": 456, "message": "world"
        }

    # --- reply messages: same text, different reply targets -> no repeat ---

    def test_reply_same_text_diff_target_no_repeat(self, bot):
        plugin = RepeaterPlugin()
        event_a = _make_group_event(111, "[CQ:reply,id=1000]hello")
        event_b = _make_group_event(222, "[CQ:reply,id=2000]hello")

        bot.msg_store.record(event_a)
        asyncio.run(plugin.handle(event_a, bot))
        bot.msg_store.record(event_b)
        asyncio.run(plugin.handle(event_b, bot))
        # Different raw_message (reply IDs differ) -> no repeat
        assert bot.api.calls == []
