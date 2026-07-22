from __future__ import annotations

import json

import pytest

from src.adapters.qq.history_gateway import HistoryPage, QQHistoryGateway
from src.core.event_bus import EventBus
from src.core.history import HistorySyncService
from src.core.message_store import MessageStore
from src.plugin.base import Event


def _message(message_id: int, *, user_id: int = 7, text: str | None = None):
    return {
        "message_id": message_id,
        "message_seq": message_id,
        "user_id": user_id,
        "time": message_id,
        "raw_message": text or f"message-{message_id}",
        "message": [
            {"type": "text", "data": {"text": text or f"message-{message_id}"}}
        ],
        "sender": {"user_id": user_id, "nickname": f"u{user_id}"},
    }


class _Gateway:
    def __init__(self, pages=None, error=None):
        self.pages = list(pages or [])
        self.error = error
        self.calls = []

    async def group_history(self, group_id, *, message_seq=None, count=20):
        self.calls.append(("group", group_id, message_seq, count))
        if self.error:
            raise self.error
        return self.pages.pop(0)

    async def friend_history(self, user_id, *, message_seq=None, count=20):
        self.calls.append(("private", user_id, message_seq, count))
        if self.error:
            raise self.error
        return self.pages.pop(0)


@pytest.mark.asyncio
async def test_missing_history_anchor_is_an_exhausted_boundary():
    class Api:
        async def call_action_quiet(self, action, **params):
            return {
                "status": "failed",
                "retcode": -1,
                "message": "消息180604217不存在",
            }

    page = await QQHistoryGateway(Api()).group_history(
        123, message_seq=180604217, count=20
    )

    assert page.messages == []
    assert page.exhausted is True
    assert page.requested_anchor == 180604217

@pytest.mark.asyncio
async def test_backfill_is_lossless_deduplicated_and_order_independent():
    store = MessageStore(db_path=":memory:")
    store.record(
        Event(
            type="message.group",
            raw={"time": 12, "sender": {"nickname": "live"}},
            user_id=7,
            message_id=12,
            message="live-12",
            group_id=99,
            is_group=True,
        )
    )
    page = HistoryPage(
        messages=[_message(11), _message(12), _message(10)],
        requested_anchor=None,
        next_anchor=None,
        exhausted=True,
    )
    service = HistorySyncService(_Gateway([page]), store, EventBus(), page_size=20)

    result = await service.sync_conversation("group", 99)

    assert result.received == 3
    assert result.inserted == 2
    assert result.coverage == "partial"
    assert [m.message_id for m in store.query(group_id=99, n=10)] == [10, 11, 12]
    assert store.get_message_record(11)["ingestion_source"] == "backfill"
    assert store.get_message_record(11)["raw_event_json"]
    assert store.get_sync_state("group", 99)["backfill_complete"] == 1
    assert store.list_sync_gaps("group", 99)[0]["reason"] == "provider_history_boundary"


@pytest.mark.asyncio
async def test_private_backfill_uses_peer_conversation_for_both_senders():
    store = MessageStore(db_path=":memory:")
    page = HistoryPage(
        messages=[_message(2, user_id=500), _message(1, user_id=900)],
        requested_anchor=None,
        next_anchor=None,
        exhausted=True,
    )
    service = HistorySyncService(_Gateway([page]), store, EventBus())

    await service.sync_conversation("private", 500)

    records = store.recent_private(500, n=10)
    assert [item.message_id for item in records] == [1, 2]
    assert store.get_message_record(1)["conversation_id"] == 500


@pytest.mark.asyncio
async def test_page_limit_persists_cursor_and_gap():
    store = MessageStore(db_path=":memory:")
    page = HistoryPage(
        messages=[_message(20), _message(19)],
        requested_anchor=None,
        next_anchor=18,
        exhausted=False,
    )
    service = HistorySyncService(
        _Gateway([page]), store, EventBus(), page_size=2, max_pages=1
    )

    result = await service.sync_conversation("group", 3)

    assert result.coverage == "partial"
    assert json.loads(store.get_sync_state("group", 3)["cursor_json"]) == {
        "message_seq": 18
    }
    assert store.list_sync_gaps("group", 3)[0]["reason"] == "pagination_limit"


@pytest.mark.asyncio
async def test_sync_failure_records_diagnostic_gap():
    store = MessageStore(db_path=":memory:")
    service = HistorySyncService(
        _Gateway(error=RuntimeError("NapCat offline")), store, EventBus()
    )

    with pytest.raises(RuntimeError, match="offline"):
        await service.sync_conversation("group", 3)

    assert store.get_sync_state("group", 3)["last_error"] == "NapCat offline"
    assert store.list_sync_gaps("group", 3)[0]["reason"] == "napcat_error"


@pytest.mark.asyncio
async def test_backfill_discovers_resource_segments():
    store = MessageStore(db_path=":memory:")
    raw = _message(30)
    raw["raw_message"] = "[CQ:file,file=f-1]"
    raw["message"] = [{"type": "file", "data": {"file_id": "f-1", "name": "a.txt"}}]
    page = HistoryPage([raw], None, None, True)
    discovered = []
    service = HistorySyncService(
        _Gateway([page]),
        store,
        EventBus(),
        artifact_discoverer=discovered.append,
    )

    await service.sync_conversation("group", 3)

    assert discovered == [30]
