"""Tests for the sticker_react plugin – react to replied messages with stickers."""

from __future__ import annotations

import asyncio

from plugins.sticker_react import StickerReactPlugin
from src.plugin.base import Event


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


class TestStickerReact:
    def setup_method(self):
        pass

    # --- match ---

    def test_match_group_message(self):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]贴[CQ:face,id=76]")
        assert plugin.match(event) is True

    def test_match_private_message(self):
        plugin = StickerReactPlugin()
        event = _make_private_event(111, "[CQ:reply,id=100]贴[CQ:face,id=76]")
        assert plugin.match(event) is False

    # --- happy path ---

    def test_sticker_face_id(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=12345]贴[CQ:face,id=76]")

        assert plugin.match(event) is True
        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {
                "method": "call_action",
                "action": "set_msg_emoji_like",
                "params": {"message_id": 12345, "emoji_id": 76, "set": True},
            }
        ]

    def test_sticker_unicode_emoji(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=12345]贴👍")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {
                "method": "call_action",
                "action": "set_msg_emoji_like",
                "params": {"message_id": 12345, "emoji_id": ord("👍"), "set": True},
            }
        ]

    # --- self message ---

    def test_bot_own_message_ignored(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(bot.config.bot.qq, "[CQ:reply,id=100]贴[CQ:face,id=76]")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- no reply code ---

    def test_no_reply_code_ignored(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "贴[CQ:face,id=76]")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- not starting with 贴 ---

    def test_not_starting_with_tie_ignored(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]表情[CQ:face,id=76]")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- 贴 with no content after ---

    def test_tie_with_nothing_after_ignored(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]贴")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- 贴 followed by only whitespace ---

    def test_tie_with_only_whitespace_ignored(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=100]贴   ")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == []

    # --- whitespace between 贴 and face ---

    def test_tie_space_face(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=12345]贴 [CQ:face,id=76]")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {
                "method": "call_action",
                "action": "set_msg_emoji_like",
                "params": {"message_id": 12345, "emoji_id": 76, "set": True},
            }
        ]

    # --- reply + at mention + 贴 + face ---

    def test_reply_with_at_and_face(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=555][CQ:at,qq=123]贴[CQ:face,id=1]")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {
                "method": "call_action",
                "action": "set_msg_emoji_like",
                "params": {"message_id": 555, "emoji_id": 1, "set": True},
            }
        ]

    # --- handle always returns False ---

    def test_handle_always_returns_false(self, bot):
        plugin = StickerReactPlugin()

        event_react = _make_group_event(111, "[CQ:reply,id=1]贴[CQ:face,id=76]")
        assert asyncio.run(plugin.handle(event_react, bot)) is False

        event_noop = _make_group_event(111, "hello")
        assert asyncio.run(plugin.handle(event_noop, bot)) is False

    # --- API exception ---

    def test_api_exception_does_not_crash(self, bot):
        plugin = StickerReactPlugin()
        orig_call = bot.api.call_action

        async def _fake_call(action: str, **params):
            bot.api.calls.append(
                {"method": "call_action", "action": action, "params": params}
            )
            raise RuntimeError("API error")

        bot.api.call_action = _fake_call  # type: ignore[method-assign]

        event = _make_group_event(111, "[CQ:reply,id=999]贴[CQ:face,id=76]")
        assert asyncio.run(plugin.handle(event, bot)) is False
        assert len(bot.api.calls) == 1

        bot.api.call_action = orig_call  # type: ignore[method-assign]

    # --- face with extra text ---

    def test_face_with_extra_text(self, bot):
        plugin = StickerReactPlugin()
        event = _make_group_event(111, "[CQ:reply,id=12345]贴[CQ:face,id=76]谢谢")

        assert asyncio.run(plugin.handle(event, bot)) is False
        assert bot.api.calls == [
            {
                "method": "call_action",
                "action": "set_msg_emoji_like",
                "params": {"message_id": 12345, "emoji_id": 76, "set": True},
            }
        ]
