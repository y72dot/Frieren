"""Tests for the essence plugin – set / remove group essence via reply."""

from __future__ import annotations

import asyncio

from src.plugin.base import Event

from plugins.essence import EssencePlugin


def _make_group_event(user_id: int, message: str, group_id: int = 456) -> Event:
    return Event(
        type="message.group",
        user_id=user_id,
        message_id=1,
        message=message,
        group_id=group_id,
        is_group=True,
    )


def _make_private_event(user_id: int, message: str) -> Event:
    return Event(
        type="message.private",
        user_id=user_id,
        message_id=1,
        message=message,
        is_group=False,
    )


class TestEssence:
    def setup_method(self):
        pass

    # --- match ---

    def test_match_group_message(self):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]设精")
        assert plugin.match(event) is True

    def test_match_private_message(self):
        plugin = EssencePlugin()
        event = _make_private_event(111, "[CQ:reply,id=100]设精")
        assert plugin.match(event) is False

    # --- basic happy path ---

    def test_set_essence(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=12345]设精")

        assert plugin.match(event) is True
        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {"method": "set_essence_msg", "message_id": 12345}
        ]

    def test_delete_essence(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=67890]寸止")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {"method": "delete_essence_msg", "message_id": 67890}
        ]

    # --- boundary: no reply CQ code ---

    def test_no_reply_code_ignored(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "设精")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- boundary: wrong keyword ---

    def test_reply_wrong_keyword_ignored(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]hello")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- boundary: reply with extra text after keyword ---

    def test_reply_keyword_not_exact_ignored(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]设精了吗")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- boundary: empty text after reply code ---

    def test_reply_empty_text_ignored(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- boundary: whitespace around keyword ---

    def test_reply_keyword_with_whitespace(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]  设精  ")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {"method": "set_essence_msg", "message_id": 100}
        ]

    # --- real-world: reply + at mention + keyword ---

    def test_reply_with_at_and_keyword(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=555][CQ:at,qq=123]设精")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {"method": "set_essence_msg", "message_id": 555}
        ]

    def test_reply_with_at_and_delete_keyword(self, bot):
        plugin = EssencePlugin()
        event = _make_group_event(111, "[CQ:reply,id=777][CQ:at,qq=456] 寸止")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {"method": "delete_essence_msg", "message_id": 777}
        ]

    # --- behavior: does not consume event ---

    def test_handle_always_returns_false(self, bot):
        plugin = EssencePlugin()

        event_set = _make_group_event(111, "[CQ:reply,id=1]设精")
        assert asyncio.run(plugin.handle(event_set, bot)) is False

        event_noop = _make_group_event(111, "hello")
        assert asyncio.run(plugin.handle(event_noop, bot)) is False

    # --- boundary: reply id with negative number (shouldn't happen but parse safely) ---

    def test_reply_with_text_containing_cq_like_pattern(self, bot):
        plugin = EssencePlugin()
        # Message contains CQ-like text but the keyword isn't exact
        event = _make_group_event(111, "[CQ:reply,id=100]设精啦")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- scenario: multiple keywords in sequence ---

    def test_set_then_delete(self, bot):
        plugin = EssencePlugin()

        event1 = _make_group_event(111, "[CQ:reply,id=111]设精")
        asyncio.run(plugin.handle(event1, bot))
        assert bot.api.calls == [
            {"method": "set_essence_msg", "message_id": 111}
        ]

        event2 = _make_group_event(222, "[CQ:reply,id=111]寸止")
        asyncio.run(plugin.handle(event2, bot))
        assert len(bot.api.calls) == 2
        assert bot.api.calls[1] == {
            "method": "delete_essence_msg", "message_id": 111
        }

    # --- error feedback: permission denied ---

    def test_set_essence_permission_error_notifies_group(self, bot):
        plugin = EssencePlugin()
        # Make the mock return a permission error
        orig_set = bot.api.set_essence_msg

        async def _fake_set(message_id: int):
            bot.api.calls.append({"method": "set_essence_msg", "message_id": message_id})
            return {"result": {"errorCode": 10003}}

        bot.api.set_essence_msg = _fake_set  # type: ignore[method-assign]

        event = _make_group_event(111, "[CQ:reply,id=999]设精")
        asyncio.run(plugin.handle(event, bot))
        assert len(bot.api.calls) == 2
        assert bot.api.calls[0] == {"method": "set_essence_msg", "message_id": 999}
        assert bot.api.calls[1]["method"] == "send_group_msg"
        assert "权限不足" in bot.api.calls[1]["message"]

        bot.api.set_essence_msg = orig_set  # type: ignore[method-assign]

    def test_delete_essence_permission_error_notifies_group(self, bot):
        plugin = EssencePlugin()
        orig_delete = bot.api.delete_essence_msg

        async def _fake_delete(message_id: int):
            bot.api.calls.append({"method": "delete_essence_msg", "message_id": message_id})
            return {"result": {"errorCode": 10003}}

        bot.api.delete_essence_msg = _fake_delete  # type: ignore[method-assign]

        event = _make_group_event(111, "[CQ:reply,id=777]寸止")
        asyncio.run(plugin.handle(event, bot))
        assert len(bot.api.calls) == 2
        assert bot.api.calls[0] == {"method": "delete_essence_msg", "message_id": 777}
        assert bot.api.calls[1]["method"] == "send_group_msg"
        assert "权限不足" in bot.api.calls[1]["message"]

        bot.api.delete_essence_msg = orig_delete  # type: ignore[method-assign]

    # --- error boundary: unknown errorCode ---

    def test_set_essence_unknown_error_notifies_group(self, bot):
        plugin = EssencePlugin()
        orig_set = bot.api.set_essence_msg

        async def _fake_set(message_id: int):
            bot.api.calls.append({"method": "set_essence_msg", "message_id": message_id})
            return {"result": {"errorCode": 99999}}

        bot.api.set_essence_msg = _fake_set  # type: ignore[method-assign]

        event = _make_group_event(111, "[CQ:reply,id=1]设精")
        asyncio.run(plugin.handle(event, bot))
        assert "未知错误" in bot.api.calls[1]["message"]

        bot.api.set_essence_msg = orig_set  # type: ignore[method-assign]
