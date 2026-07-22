"""Lossless QQ/CQ adapter tests."""

from __future__ import annotations

import json

from src.adapters.qq import (
    extract_message_array,
    scan_cq,
    serialize_raw_event,
)
from src.core.event_bus import EventBus


def test_scan_cq_preserves_unknown_type_and_raw_text() -> None:
    message = (
        "before[CQ:future_kind,z=1,file=a&#44;b.txt,flag=x=y]"
        "after[CQ:image,file=abc]"
    )

    refs = scan_cq(message)

    assert [ref.type for ref in refs] == ["future_kind", "image"]
    assert refs[0].raw == "[CQ:future_kind,z=1,file=a&#44;b.txt,flag=x=y]"
    assert refs[0].attributes == {
        "z": "1",
        "file": "a&#44;b.txt",
        "flag": "x=y",
    }
    assert message[refs[0].start : refs[0].end] == refs[0].raw


def test_malformed_cq_is_left_as_plain_text() -> None:
    assert scan_cq("hello [CQ:file,file=a.txt") == []


def test_sdk_style_event_serialization_and_segments() -> None:
    class FakeEvent:
        raw_message = "[CQ:future,x=1]"
        message = [{"type": "future", "data": {"x": 1, "extra": "kept"}}]

        def to_dict(self):
            return {
                "post_type": "message",
                "raw_message": self.raw_message,
                "message": self.message,
                "new_field": {"nested": True},
            }

    raw = FakeEvent()
    serialized = json.loads(serialize_raw_event(raw))

    assert serialized["new_field"] == {"nested": True}
    assert extract_message_array(raw) == raw.message


def test_event_bus_keeps_raw_cq_and_message_array() -> None:
    raw = {
        "post_type": "message",
        "message_type": "group",
        "message_id": 101,
        "group_id": 456,
        "user_id": 123,
        "time": 1000,
        "raw_message": "hello[CQ:future_kind,value=1]",
        "message": [
            {"type": "text", "data": {"text": "hello"}},
            {"type": "future_kind", "data": {"value": 1, "unknown": "kept"}},
        ],
    }

    event = EventBus().parse(raw)

    assert event is not None
    assert event.message == raw["raw_message"]
    assert event.raw_message == raw["raw_message"]
    assert event.message_array == raw["message"]
    assert json.loads(event.raw_event_json)["message"][1]["data"]["unknown"] == "kept"


def test_synthetic_forward_text_does_not_replace_raw_message() -> None:
    raw = {
        "post_type": "message",
        "message_type": "group",
        "message_id": 102,
        "group_id": 456,
        "user_id": 123,
        "raw_message": "",
        "message": [{"type": "forward", "data": {"id": "forward-1"}}],
    }

    event = EventBus().parse(raw)

    assert event is not None
    assert event.message == "[CQ:forward,id=forward-1]"
    assert event.raw_message == ""
    assert event.message_array == raw["message"]
