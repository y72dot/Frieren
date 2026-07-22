from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecentConversation:
    conversation_type: str
    conversation_id: int
    last_message_id: int | None
    last_message_time: int | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class HistoryPage:
    messages: list[dict[str, Any]]
    requested_anchor: int | None
    next_anchor: int | None
    exhausted: bool


class QQHistoryGateway:
    """The only module that knows NapCat history action parameters."""

    def __init__(self, api: Any) -> None:
        self.api = api

    async def recent_contacts(self, count: int = 50) -> list[RecentConversation]:
        response = await self.api.call_action("get_recent_contact", count=count)
        data = _response_data(response)
        if not isinstance(data, list):
            return []
        contacts: list[RecentConversation] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            conversation_id = _integer(raw.get("peerUin") or raw.get("peer_id"))
            if conversation_id is None:
                continue
            chat_type = _integer(raw.get("chatType") or raw.get("chat_type"))
            contacts.append(
                RecentConversation(
                    conversation_type="group" if chat_type == 2 else "private",
                    conversation_id=conversation_id,
                    last_message_id=_integer(raw.get("msgId") or raw.get("message_id")),
                    last_message_time=_integer(raw.get("msgTime") or raw.get("time")),
                    raw=raw,
                )
            )
        return contacts

    async def group_history(
        self, group_id: int, *, message_seq: int | None = None, count: int = 20
    ) -> HistoryPage:
        return await self._history(
            "get_group_msg_history",
            {"group_id": group_id},
            message_seq=message_seq,
            count=count,
        )

    async def friend_history(
        self, user_id: int, *, message_seq: int | None = None, count: int = 20
    ) -> HistoryPage:
        return await self._history(
            "get_friend_msg_history",
            {"user_id": user_id},
            message_seq=message_seq,
            count=count,
        )

    async def _history(
        self,
        action: str,
        identity: dict[str, Any],
        *,
        message_seq: int | None,
        count: int,
    ) -> HistoryPage:
        params = {
            **identity,
            "count": count,
            "reverse_order": False,
            "reverseOrder": False,
            "disable_get_url": False,
            "parse_mult_msg": True,
            "quick_reply": False,
        }
        if message_seq is not None:
            params["message_seq"] = message_seq
        response = await self.api.call_action(action, **params)
        data = _response_data(response)
        messages = data.get("messages", []) if isinstance(data, dict) else []
        messages = [item for item in messages if isinstance(item, dict)]
        anchors = [
            value
            for item in messages
            if (value := _integer(item.get("message_seq") or item.get("message_id")))
            is not None
        ]
        next_anchor = min(anchors) - 1 if anchors else None
        return HistoryPage(
            messages=messages,
            requested_anchor=message_seq,
            next_anchor=next_anchor,
            exhausted=len(messages) < count or next_anchor is None,
        )


def _response_data(response: Any) -> Any:
    if not isinstance(response, dict):
        raise RuntimeError("NapCat returned a non-object history response")
    if response.get("status") == "failed" or response.get("retcode") not in (None, 0):
        raise RuntimeError(
            str(
                response.get("message")
                or response.get("wording")
                or "NapCat history action failed"
            )
        )
    return response.get("data", response)


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
