from __future__ import annotations

import pytest

from src.adapters.qq.history_gateway import QQHistoryGateway


class _Api:
    def __init__(self):
        self.calls = []
        self.responses = {}

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        return self.responses.get(action, {"status": "ok", "data": {"messages": []}})


@pytest.mark.asyncio
async def test_group_history_uses_official_pagination_fields():
    api = _Api()
    api.responses["get_group_msg_history"] = {
        "status": "ok",
        "retcode": 0,
        "data": {
            "messages": [
                {"message_id": 12, "message_seq": 12},
                {"message_id": 10, "message_seq": 10},
            ]
        },
    }
    gateway = QQHistoryGateway(api)

    page = await gateway.group_history(123, message_seq=20, count=2)

    assert page.next_anchor == 9
    assert not page.exhausted
    assert api.calls == [
        (
            "get_group_msg_history",
            {
                "group_id": 123,
                "count": 2,
                "reverse_order": False,
                "reverseOrder": False,
                "disable_get_url": False,
                "parse_mult_msg": True,
                "quick_reply": False,
                "message_seq": 20,
            },
        )
    ]


@pytest.mark.asyncio
async def test_recent_contacts_normalizes_group_and_private():
    api = _Api()
    api.responses["get_recent_contact"] = {
        "status": "ok",
        "data": [
            {"peerUin": "100", "chatType": 2, "msgId": "8", "msgTime": "7"},
            {"peerUin": "200", "chatType": 1, "msgId": "9", "msgTime": "6"},
        ],
    }
    gateway = QQHistoryGateway(api)

    contacts = await gateway.recent_contacts(10)

    assert [(item.conversation_type, item.conversation_id) for item in contacts] == [
        ("group", 100),
        ("private", 200),
    ]


@pytest.mark.asyncio
async def test_gateway_rejects_napcat_failure():
    api = _Api()
    api.responses["get_friend_msg_history"] = {
        "status": "failed",
        "retcode": 1404,
        "message": "not found",
    }
    gateway = QQHistoryGateway(api)

    with pytest.raises(RuntimeError, match="not found"):
        await gateway.friend_history(1)
