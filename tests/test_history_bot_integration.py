from __future__ import annotations

import pytest

from src.core.bot import Bot
from src.core.message_store import MessageStore


class _HistoryApi:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if self.fail:
            raise RuntimeError("NapCat unavailable")
        if action == "get_recent_contact":
            return {
                "status": "ok",
                "data": [{"peerUin": "123", "chatType": 2, "msgId": "10"}],
            }
        if action == "get_group_msg_history":
            return {
                "status": "ok",
                "data": {
                    "messages": [
                        {
                            "message_id": 10,
                            "message_seq": 10,
                            "user_id": 8,
                            "time": 10,
                            "raw_message": "offline message",
                            "message": [
                                {"type": "text", "data": {"text": "offline message"}}
                            ],
                            "sender": {"user_id": 8, "nickname": "alice"},
                        }
                    ]
                },
            }
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_connect_sync_imports_offline_message(bot_config):
    bot = Bot(config=bot_config)
    old_store = bot.msg_store
    bot.msg_store = MessageStore(db_path=":memory:")
    old_store.close()
    bot.api = _HistoryApi()

    await bot._sync_history_on_connect()

    record = bot.msg_store.get_message_record(10)
    assert record["content"] == "offline message"
    assert record["ingestion_source"] == "backfill"
    assert bot.api.calls[0][0] == "get_recent_contact"


@pytest.mark.asyncio
async def test_connect_sync_failure_does_not_escape(bot_config):
    bot = Bot(config=bot_config)
    old_store = bot.msg_store
    bot.msg_store = MessageStore(db_path=":memory:")
    old_store.close()
    bot.api = _HistoryApi(fail=True)

    await bot._sync_history_on_connect()

    assert bot.api.calls[0][0] == "get_recent_contact"
