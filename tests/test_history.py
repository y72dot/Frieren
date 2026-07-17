from __future__ import annotations

import json

import pytest

from src.plugin.base import Event
from plugins.history import HistoryPlugin


# -------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------

class _FakeNapcatGroupEvent:
    def to_dict(self) -> dict:
        return {
            "post_type": "message",
            "message_type": "group",
            "user_id": "111",
            "group_id": 456,
            "raw_message": "hello",
        }


class _FakeNapcatPrivateEvent:
    def to_dict(self) -> dict:
        return {
            "post_type": "message",
            "message_type": "private",
            "user_id": "222",
            "raw_message": "hi",
        }


# -------------------------------------------------------------------
# match() tests
# -------------------------------------------------------------------

class TestMatch:
    def test_match_group_message(self):
        plugin = HistoryPlugin()
        event = Event(type="message.group", user_id=1, group_id=100)
        assert plugin.match(event) is True

    def test_match_private_message(self):
        plugin = HistoryPlugin()
        event = Event(type="message.private", user_id=1)
        assert plugin.match(event) is True

    def test_match_notice_event(self):
        plugin = HistoryPlugin()
        event = Event(type="notice.notify", user_id=1, group_id=100)
        assert plugin.match(event) is True

    def test_match_request_event(self):
        plugin = HistoryPlugin()
        event = Event(type="request.friend", user_id=1)
        assert plugin.match(event) is True

    def test_match_meta_event(self):
        plugin = HistoryPlugin()
        event = Event(type="meta.heartbeat", user_id=0)
        assert plugin.match(event) is True


# -------------------------------------------------------------------
# _serialize() tests
# -------------------------------------------------------------------

class TestSerialize:
    def test_serialize_napcat_object_with_to_dict(self):
        raw = _FakeNapcatGroupEvent()
        result = HistoryPlugin._serialize(raw)
        data = json.loads(result)
        assert data["post_type"] == "message"
        assert data["message_type"] == "group"

    def test_serialize_plain_dict(self):
        raw = {"post_type": "message", "message_type": "group", "user_id": 123}
        result = HistoryPlugin._serialize(raw)
        data = json.loads(result)
        assert data["user_id"] == 123

    def test_serialize_chinese_characters_preserved(self):
        raw = {"message": "你好世界"}
        result = HistoryPlugin._serialize(raw)
        assert "你好世界" in result
        assert "\\u" not in result

    def test_serialize_unsupported_type_returns_none(self):
        assert HistoryPlugin._serialize(12345) is None

    def test_serialize_unsupported_list_returns_none(self):
        assert HistoryPlugin._serialize([1, 2, 3]) is None


# -------------------------------------------------------------------
# handle() tests
# -------------------------------------------------------------------

class TestHandle:
    @pytest.mark.asyncio
    async def test_handle_writes_jsonl_from_napcat_object(self, bot, tmp_path):
        plugin = HistoryPlugin()
        plugin.log_path = tmp_path / "history.log"

        raw = _FakeNapcatGroupEvent()
        event = Event(
            type="message.group", raw=raw, user_id=111,
            message="hello", group_id=456, is_group=True,
        )

        result = await plugin.handle(event, bot)
        assert result is False

        lines = plugin.log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["post_type"] == "message"
        assert data["user_id"] == "111"
        assert data["group_id"] == 456

    @pytest.mark.asyncio
    async def test_handle_writes_jsonl_from_dict_raw(self, bot, tmp_path):
        plugin = HistoryPlugin()
        plugin.log_path = tmp_path / "history.log"

        raw = {"post_type": "message", "message_type": "private", "user_id": 222}
        event = Event(
            type="message.private", raw=raw, user_id=222,
            message="hi", is_group=False,
        )

        await plugin.handle(event, bot)
        lines = plugin.log_path.read_text(encoding="utf-8").strip().split("\n")
        data = json.loads(lines[0])
        assert data["message_type"] == "private"

    @pytest.mark.asyncio
    async def test_handle_never_consumes_event(self, bot):
        plugin = HistoryPlugin()
        event = Event(type="message.group", raw={"x": 1}, user_id=1, group_id=100)
        result = await plugin.handle(event, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_appends_multiple_events(self, bot, tmp_path):
        plugin = HistoryPlugin()
        plugin.log_path = tmp_path / "history.log"

        for i in range(3):
            raw = _FakeNapcatGroupEvent()
            event = Event(type="message.group", raw=raw, user_id=i, group_id=100)
            await plugin.handle(event, bot)

        lines = plugin.log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_handle_creates_parent_directory(self, bot, tmp_path):
        plugin = HistoryPlugin()
        plugin.log_path = tmp_path / "deep" / "nested" / "history.log"

        raw = _FakeNapcatGroupEvent()
        event = Event(type="message.group", raw=raw, user_id=1, group_id=100)
        await plugin.handle(event, bot)

        assert plugin.log_path.exists()
        assert plugin.log_path.read_text(encoding="utf-8").strip() != ""

    @pytest.mark.asyncio
    async def test_handle_skips_unsupported_raw_type(self, bot, tmp_path):
        plugin = HistoryPlugin()
        plugin.log_path = tmp_path / "history.log"

        event = Event(type="message.group", raw=12345, user_id=1, group_id=100)
        result = await plugin.handle(event, bot)
        assert result is False
        assert not plugin.log_path.exists()

    @pytest.mark.asyncio
    async def test_handle_writes_private_message(self, bot, tmp_path):
        plugin = HistoryPlugin()
        plugin.log_path = tmp_path / "history.log"

        raw = _FakeNapcatPrivateEvent()
        event = Event(
            type="message.private", raw=raw, user_id=222,
            message="hi", is_group=False,
        )

        await plugin.handle(event, bot)
        lines = plugin.log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["post_type"] == "message"
        assert data["message_type"] == "private"


# -------------------------------------------------------------------
# integration tests
# -------------------------------------------------------------------

class TestIntegration:
    def test_plugin_protocol_compliance(self):
        plugin = HistoryPlugin()
        assert plugin.name == "history"
        assert plugin.priority == -90
        assert callable(plugin.match)
        assert callable(plugin.handle)
