from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.adapters.qq import QQHistoryGateway, serialize_raw_event
from src.core.message_store import MessageStore, StoredMessage


@dataclass(frozen=True)
class HistorySyncResult:
    conversation_type: str
    conversation_id: int
    pages: int
    received: int
    inserted: int
    coverage: str
    stopped_reason: str


@dataclass(frozen=True)
class HistoryQueryResult:
    messages: list[StoredMessage]
    coverage: str
    gaps: list[dict[str, Any]]
    backfilled: int = 0


class HistorySyncService:
    """Imports NapCat history through the same lossless message projection."""

    def __init__(
        self,
        gateway: QQHistoryGateway,
        message_store: MessageStore,
        event_bus: Any,
        *,
        artifact_discoverer: Any = None,
        page_size: int = 20,
        max_pages: int = 3,
    ) -> None:
        self.gateway = gateway
        self.message_store = message_store
        self.event_bus = event_bus
        self.artifact_discoverer = artifact_discoverer
        self.page_size = page_size
        self.max_pages = max_pages

    async def sync_recent(self, count: int = 50) -> list[HistorySyncResult]:
        contacts = await self.gateway.recent_contacts(count)
        results: list[HistorySyncResult] = []
        for contact in contacts:
            try:
                results.append(
                    await self.sync_conversation(
                        contact.conversation_type,
                        contact.conversation_id,
                        max_pages=1,
                    )
                )
            except Exception as exc:
                self._record_error(
                    contact.conversation_type, contact.conversation_id, exc
                )
        return results

    async def sync_conversation(
        self,
        conversation_type: str,
        conversation_id: int,
        *,
        max_pages: int | None = None,
        continue_from_cursor: bool = False,
    ) -> HistorySyncResult:
        if conversation_type not in {"group", "private"}:
            raise ValueError("conversation_type must be group or private")
        page_limit = max_pages if max_pages is not None else self.max_pages
        state = self.message_store.get_sync_state(conversation_type, conversation_id)
        local_ids = self._local_ids(conversation_type, conversation_id)
        anchor: int | None = None
        if continue_from_cursor and state and state.get("cursor_json"):
            try:
                anchor = json.loads(state["cursor_json"]).get("message_seq")
            except (json.JSONDecodeError, AttributeError):
                anchor = None

        pages = received = inserted = 0
        coverage = "partial"
        stopped_reason = "page_limit"
        provider_exhausted = False
        try:
            for _ in range(max(page_limit, 1)):
                page = (
                    await self.gateway.group_history(
                        conversation_id, message_seq=anchor, count=self.page_size
                    )
                    if conversation_type == "group"
                    else await self.gateway.friend_history(
                        conversation_id, message_seq=anchor, count=self.page_size
                    )
                )
                pages += 1
                received += len(page.messages)
                saw_existing = False
                for raw in page.messages:
                    normalized = _normalize_history_message(
                        raw, conversation_type, conversation_id
                    )
                    message_id = _integer(normalized.get("message_id"))
                    if message_id is None:
                        continue
                    if message_id in local_ids:
                        saw_existing = True
                    event = self.event_bus.parse(normalized)
                    if event is None:
                        continue
                    event.ingestion_source = "backfill"
                    if conversation_type == "private":
                        event.peer_id = conversation_id
                    event.raw_event_json = serialize_raw_event(normalized)
                    was_new = self.message_store.get_message_record(message_id) is None
                    self.message_store.record(event)
                    if was_new:
                        inserted += 1
                    if self.artifact_discoverer is not None:
                        self.artifact_discoverer(message_id)
                    local_ids.add(message_id)

                anchor = page.next_anchor
                if page.exhausted:
                    provider_exhausted = True
                    stopped_reason = "provider_exhausted"
                    self.message_store.resolve_sync_gaps(
                        conversation_type, conversation_id
                    )
                    self.message_store.record_sync_gap(
                        conversation_type,
                        conversation_id,
                        "provider_history_boundary",
                    )
                    break
                if saw_existing:
                    stopped_reason = "local_overlap"
                    break
                if anchor is None:
                    stopped_reason = "no_cursor"
                    break
            else:
                self.message_store.record_sync_gap(
                    conversation_type,
                    conversation_id,
                    "pagination_limit",
                )
            self.message_store.update_sync_status(
                conversation_type,
                conversation_id,
                cursor={"message_seq": anchor} if anchor is not None else {},
                complete=provider_exhausted,
                error=None,
            )
        except Exception as exc:
            self._record_error(conversation_type, conversation_id, exc)
            raise

        logger.info(
            "History sync: type={} id={} pages={} received={} inserted={} coverage={} stop={}",
            conversation_type,
            conversation_id,
            pages,
            received,
            inserted,
            coverage,
            stopped_reason,
        )
        return HistorySyncResult(
            conversation_type,
            conversation_id,
            pages,
            received,
            inserted,
            coverage,
            stopped_reason,
        )

    def _record_error(
        self, conversation_type: str, conversation_id: int, exc: Exception
    ) -> None:
        error = str(exc)[:1000]
        self.message_store.update_sync_status(
            conversation_type, conversation_id, error=error
        )
        self.message_store.record_sync_gap(
            conversation_type, conversation_id, "napcat_error"
        )
        logger.opt(exception=True).error(
            f"History sync failed: type={conversation_type} id={conversation_id}"
        )

    def _local_ids(self, conversation_type: str, conversation_id: int) -> set[int]:
        rows = self.message_store.connection.execute(
            "SELECT message_id FROM messages WHERE conversation_type=? "
            "AND conversation_id=?",
            (conversation_type, conversation_id),
        ).fetchall()
        return {int(row[0]) for row in rows}


class HistoryQueryService:
    """Runs local queries first and performs bounded backfill when insufficient."""

    def __init__(
        self,
        message_store: MessageStore,
        sync_service: HistorySyncService,
        *,
        query_backfill: bool = True,
    ) -> None:
        self.message_store = message_store
        self.sync_service = sync_service
        self.query_backfill = query_backfill

    async def query(
        self,
        conversation_type: str,
        conversation_id: int,
        **criteria: Any,
    ) -> HistoryQueryResult:
        criteria = dict(criteria)
        limit = int(criteria.pop("n", 10))
        messages = self._local_query(
            conversation_type, conversation_id, n=limit, **criteria
        )
        state = self.message_store.get_sync_state(conversation_type, conversation_id)
        gaps = self.message_store.list_sync_gaps(conversation_type, conversation_id)
        exact_hit = criteria.get("message_id") is not None and bool(messages)
        enough = len(messages) >= limit
        complete_archive = bool(state and state.get("backfill_complete"))
        if exact_hit or (enough and not gaps) or (complete_archive and not gaps):
            coverage = "complete"
            return HistoryQueryResult(messages, coverage, gaps)

        backfilled = 0
        if self.query_backfill:
            try:
                result = await self.sync_service.sync_conversation(
                    conversation_type,
                    conversation_id,
                    continue_from_cursor=True,
                )
                backfilled = result.inserted
                messages = self._local_query(
                    conversation_type, conversation_id, n=limit, **criteria
                )
            except Exception:
                pass

        state = self.message_store.get_sync_state(conversation_type, conversation_id)
        gaps = self.message_store.list_sync_gaps(conversation_type, conversation_id)
        exact_hit = criteria.get("message_id") is not None and bool(messages)
        enough = len(messages) >= limit
        complete_archive = bool(state and state.get("backfill_complete"))
        coverage = (
            "complete"
            if exact_hit or (complete_archive and not gaps) or (enough and not gaps)
            else "partial"
            if state or messages or gaps
            else "unknown"
        )
        return HistoryQueryResult(messages, coverage, gaps, backfilled)

    def _local_query(
        self,
        conversation_type: str,
        conversation_id: int,
        **criteria: Any,
    ) -> list[StoredMessage]:
        return self.message_store.query(
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            is_group=conversation_type == "group",
            **criteria,
        )


def _normalize_history_message(
    raw: dict[str, Any], conversation_type: str, conversation_id: int
) -> dict[str, Any]:
    result = dict(raw)
    result["post_type"] = "message"
    result["message_type"] = conversation_type
    if conversation_type == "group":
        result["group_id"] = conversation_id
    sender = result.get("sender") if isinstance(result.get("sender"), dict) else {}
    sender_id = _integer(result.get("user_id")) or _integer(sender.get("user_id"))
    result["user_id"] = sender_id or conversation_id
    if "raw_message" not in result:
        message = result.get("message", "")
        result["raw_message"] = message if isinstance(message, str) else ""
    return result


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
