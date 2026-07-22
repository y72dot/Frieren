from __future__ import annotations

import pytest

from src.core.history import HistoryQueryService, HistorySyncResult
from src.core.message_store import MessageStore
from src.plugin.base import Event


def _seed(store: MessageStore, message_id: int, group_id: int = 8):
    store.record(
        Event(
            type="message.group",
            raw={"time": message_id, "sender": {"nickname": "n"}},
            user_id=1,
            message_id=message_id,
            message=f"m{message_id}",
            group_id=group_id,
            is_group=True,
        )
    )


class _Sync:
    def __init__(self, store, insert=False):
        self.store = store
        self.insert = insert
        self.calls = []

    async def sync_conversation(self, conversation_type, conversation_id, **kwargs):
        self.calls.append((conversation_type, conversation_id, kwargs))
        if self.insert:
            _seed(self.store, 2, conversation_id)
        return HistorySyncResult(
            conversation_type,
            conversation_id,
            1,
            1,
            int(self.insert),
            "partial",
            "page_limit",
        )


@pytest.mark.asyncio
async def test_local_query_does_not_call_napcat_when_enough_rows():
    store = MessageStore(db_path=":memory:")
    _seed(store, 1)
    _seed(store, 2)
    sync = _Sync(store)
    query = HistoryQueryService(store, sync)

    result = await query.query("group", 8, n=2)

    assert [item.message_id for item in result.messages] == [1, 2]
    assert result.coverage == "complete"
    assert sync.calls == []


@pytest.mark.asyncio
async def test_insufficient_local_query_backfills_then_requeries_database():
    store = MessageStore(db_path=":memory:")
    _seed(store, 1)
    sync = _Sync(store, insert=True)
    query = HistoryQueryService(store, sync)

    result = await query.query("group", 8, n=2)

    assert [item.message_id for item in result.messages] == [1, 2]
    assert result.backfilled == 1
    assert sync.calls[0][2]["continue_from_cursor"] is True


@pytest.mark.asyncio
async def test_disabled_backfill_reports_partial_coverage():
    store = MessageStore(db_path=":memory:")
    _seed(store, 1)
    sync = _Sync(store)
    query = HistoryQueryService(store, sync, query_backfill=False)

    result = await query.query("group", 8, n=3)

    assert result.coverage == "partial"
    assert sync.calls == []
