"""Tests for Event model."""

from src.plugin.base import Event


def test_event_creation():
    e = Event(
        type="message.group", user_id=123, message="/ping", group_id=456, is_group=True
    )
    assert e.type == "message.group"
    assert e.user_id == 123
    assert e.message == "/ping"
    assert e.group_id == 456
    assert e.is_group is True


def test_event_defaults():
    e = Event(type="notice.heartbeat", user_id=0)
    assert e.message == ""
    assert e.group_id is None
    assert e.is_group is False


def test_event_raw_stores_arbitrary_object():
    raw = {"post_type": "message", "message_type": "group"}
    e = Event(type="message.group", raw=raw, user_id=1)
    assert e.raw is raw
