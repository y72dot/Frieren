"""Phase-two journal, projection, migration, and losslessness tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.adapters.qq import serialize_raw_event
from src.core.event_bus import EventBus
from src.core.message_store import MessageStore
from src.plugin.base import Event


def _raw_message(message_id: int = 1001) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "message_id": message_id,
        "group_id": 456,
        "user_id": 123,
        "self_id": 999,
        "time": 1700000000,
        "raw_message": "说明[CQ:file,file=report.xlsx,file_id=f-1]",
        "message": [
            {"type": "text", "data": {"text": "说明"}},
            {
                "type": "file",
                "data": {"file": "report.xlsx", "file_id": "f-1", "extra": "kept"},
            },
            {"type": "future_kind", "data": {"unknown": [1, 2, 3]}},
        ],
        "sender": {"nickname": "Alice", "card": "A"},
        "new_napcat_field": {"kept": True},
    }


def _event(raw: dict, *, source: str = "live") -> Event:
    event = EventBus().parse(raw)
    assert event is not None
    event.ingestion_source = source
    return event


def test_journal_message_and_segments_are_lossless() -> None:
    store = MessageStore(db_path=":memory:")
    raw = _raw_message()

    event_id = store.record(_event(raw), trace_id="trace-1")

    journal = store.get_journal_event(event_id)
    record = store.get_message_record(raw["message_id"])
    segments = store.get_segments(raw["message_id"])
    assert journal is not None and journal["projected"] == 1
    assert journal["trace_id"] == "trace-1"
    assert json.loads(journal["raw_json"])["new_napcat_field"] == {"kept": True}
    assert record is not None
    assert record["raw_message"] == raw["raw_message"]
    assert json.loads(record["message_array_json"]) == raw["message"]
    assert json.loads(record["raw_event_json"])["new_napcat_field"]["kept"] is True
    assert [segment.segment_type for segment in segments] == [
        "text",
        "file",
        "future_kind",
    ]
    assert json.loads(segments[2].raw_segment_json)["data"]["unknown"] == [1, 2, 3]
    assert segments[1].raw_cq == "[CQ:file,file=report.xlsx,file_id=f-1]"


def test_projection_failure_leaves_recoverable_journal_row() -> None:
    store = MessageStore(db_path=":memory:")
    raw = _raw_message(1002)
    event = _event(raw)
    event.message_array = [{"type": "file", "data": {"invalid": object()}}]
    event.raw_event_json = serialize_raw_event(raw)

    with pytest.raises(TypeError):
        store.record(event)

    pending = store.unprojected_events()
    assert len(pending) == 1
    assert pending[0]["projected"] == 0
    assert pending[0]["projection_error"]
    assert store.get_message_record(1002) is None

    class BotStub:
        msg_store = store

    recovered = EventBus().recover_unprojected(BotStub())
    assert recovered == 1
    assert store.unprojected_events() == []
    assert store.get_message_record(1002) is not None


def test_live_and_backfill_deduplicate_message_but_keep_journal_sources() -> None:
    store = MessageStore(db_path=":memory:")
    live = _raw_message(1003)
    backfill = _raw_message(1003)
    backfill["raw_message"] = "different historical representation"

    live_event_id = store.record(_event(live, source="live"))
    backfill_event_id = store.record(_event(backfill, source="backfill"))

    assert live_event_id != backfill_event_id
    assert store.stats()["journal_events"] == 2
    assert len(store.query(message_id=1003)) == 1
    record = store.get_message_record(1003)
    assert record is not None
    assert record["raw_message"] == live["raw_message"]
    assert record["ingestion_source"] == "live"
    state = store.get_sync_state("group", 456)
    assert state is not None
    assert state["last_live_event_at"] is not None
    assert state["last_backfill_at"] is not None


def test_search_uses_derived_message_array_text() -> None:
    store = MessageStore(db_path=":memory:")
    raw = _raw_message(1004)
    raw["raw_message"] = "[文件]"

    store.record(_event(raw))

    results = store.search(456, "report", n=10)
    assert [message.message_id for message in results] == [1004]


def test_legacy_messages_database_is_migrated_without_data_loss() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "messages.db"
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE messages (
                message_id INTEGER PRIMARY KEY,
                group_id INTEGER,
                user_id INTEGER NOT NULL,
                nickname TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                time INTEGER NOT NULL,
                is_group INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            """INSERT INTO messages
               (message_id, group_id, user_id, nickname, content, time, is_group)
               VALUES (1, 456, 123, 'Legacy', 'old[CQ:face,id=1]', 1000, 1)"""
        )
        conn.commit()
        conn.close()

        store = MessageStore(db_path=str(path))
        record = store.get_message_record(1)

        assert record is not None
        assert record["content"] == "old[CQ:face,id=1]"
        assert record["raw_message"] == "old[CQ:face,id=1]"
        assert record["conversation_type"] == "group"
        assert record["conversation_id"] == 456
        assert store.search(456, "old")[0].message_id == 1
        store.close()
