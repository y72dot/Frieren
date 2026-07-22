"""Convert napcat-sdk events into internal Events and route them."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.adapters.qq import (
    extract_message_array,
    extract_raw_message,
    serialize_raw_event,
)
from src.core.message_bus import BusMessage, MessageType
from src.plugin.base import Event

if TYPE_CHECKING:
    from src.core.bot import Bot


# Try importing napcat event types – they may not be installed during tests.
try:
    from napcat import (  # type: ignore[import-untyped]
        GroupMessageEvent,
        NoticeEvent,
        PrivateMessageEvent,
    )

    _NAPCAT_AVAILABLE = True
except ImportError:  # pragma: no cover
    GroupMessageEvent: Any = None  # type: ignore[no-redef]
    PrivateMessageEvent: Any = None  # type: ignore[no-redef]
    NoticeEvent: Any = None  # type: ignore[no-redef]
    _NAPCAT_AVAILABLE = False


def _extract_forward_id(raw_event: Any) -> str | None:
    """Extract the forward message ID from a napcat event's message segments.

    Returns ``None`` if the event does not contain a forward segment.
    """
    # napcat-sdk typed events: message is a list of segment objects
    if hasattr(raw_event, "message") and isinstance(raw_event.message, list):
        for seg in raw_event.message:
            if hasattr(seg, "type") and seg.type == "forward":
                data = getattr(seg, "data", {}) or {}
                return data.get("id") or data.get("message_id")
    # dict-style events: message is a list of dict segments
    if isinstance(raw_event, dict):
        msg_array = raw_event.get("message", [])
        if isinstance(msg_array, list):
            for seg in msg_array:
                if isinstance(seg, dict) and seg.get("type") == "forward":
                    data = seg.get("data", {}) or {}
                    return data.get("id") or data.get("message_id")
    return None


class EventBus:
    """Parses raw napcat-sdk events into internal :class:`Event` objects
    and dispatches them through the :class:`MessageBus`."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # parsing
    # ------------------------------------------------------------------

    def parse(self, raw_event: Any) -> Event | None:
        """Convert a napcat-sdk event to an internal :class:`Event`.

        Returns ``None`` for unknown / unhandled event types.
        """
        # typed message events (napcat-sdk >= 0.1)
        if GroupMessageEvent is not None and isinstance(raw_event, GroupMessageEvent):
            raw_message = extract_raw_message(raw_event)
            message_array = extract_message_array(raw_event)
            msg_text = raw_message
            if not msg_text:
                fwd_id = _extract_forward_id(raw_event)
                if fwd_id:
                    msg_text = f"[CQ:forward,id={fwd_id}]"
            return Event(
                type="message.group",
                raw=raw_event,
                user_id=int(raw_event.user_id),
                message_id=int(raw_event.message_id),
                message=msg_text,
                group_id=int(raw_event.group_id),
                is_group=True,
                raw_message=raw_message,
                message_array=message_array,
                raw_event_json=serialize_raw_event(raw_event),
            )

        if PrivateMessageEvent is not None and isinstance(
            raw_event, PrivateMessageEvent
        ):
            raw_message = extract_raw_message(raw_event)
            message_array = extract_message_array(raw_event)
            msg_text = raw_message
            if not msg_text:
                fwd_id = _extract_forward_id(raw_event)
                if fwd_id:
                    msg_text = f"[CQ:forward,id={fwd_id}]"
            return Event(
                type="message.private",
                raw=raw_event,
                user_id=int(raw_event.user_id),
                message_id=int(raw_event.message_id),
                message=msg_text,
                is_group=False,
                peer_id=int(raw_event.user_id),
                raw_message=raw_message,
                message_array=message_array,
                raw_event_json=serialize_raw_event(raw_event),
            )

        # typed notice events (napcat-sdk)
        if NoticeEvent is not None and isinstance(raw_event, NoticeEvent):
            return self._parse_notice_event(raw_event)

        # fallback: dict-style events (post_type-based)
        if isinstance(raw_event, dict):
            return self._parse_dict_event(raw_event)

        logger.debug(f"Unhandled event type, discarding: {type(raw_event).__name__}")
        return None

    @staticmethod
    def _parse_dict_event(data: dict[str, Any]) -> Event | None:
        post_type = data.get("post_type", "")
        if post_type == "message":
            msg_type = data.get("message_type", "")
            group_id = data.get("group_id")
            raw_message = extract_raw_message(data)
            message_array = extract_message_array(data)
            msg_text = raw_message
            if not msg_text:
                fwd_id = _extract_forward_id(data)
                if fwd_id:
                    msg_text = f"[CQ:forward,id={fwd_id}]"
            if not msg_text:
                msg_text = str(data.get("message", ""))
            logger.debug(
                f"Parsed dict event: message.{msg_type}" if msg_type else "message"
            )
            return Event(
                type=f"message.{msg_type}" if msg_type else "message",
                raw=data,
                user_id=int(data.get("user_id", 0)),
                message_id=int(data.get("message_id", 0)) or None,
                message=msg_text,
                group_id=int(group_id) if group_id is not None else None,
                is_group=msg_type == "group",
                peer_id=int(data.get("user_id", 0)) if msg_type == "private" else None,
                raw_message=raw_message,
                message_array=message_array,
                raw_event_json=serialize_raw_event(data),
            )
        elif post_type == "notice":
            notice_type = data.get("notice_type", "")
            logger.debug(f"Parsed dict event: notice.{notice_type}")
            user_id = data.get("user_id", data.get("operator_id", 0))
            group_id = data.get("group_id")
            return Event(
                type=f"notice.{notice_type}",
                raw=data,
                user_id=int(user_id) if user_id else 0,
                group_id=int(group_id) if group_id is not None else None,
                is_group=group_id is not None,
                raw_event_json=serialize_raw_event(data),
            )
        elif post_type == "request":
            req_type = data.get("request_type", "")
            logger.debug(f"Parsed dict event: request.{req_type}")
            return Event(
                type=f"request.{req_type}",
                raw=data,
                user_id=int(data.get("user_id", 0)),
                message=str(data.get("comment", "")),
                group_id=int(gid)
                if (gid := data.get("group_id")) is not None
                else None,
                is_group=gid is not None,
                raw_event_json=serialize_raw_event(data),
            )
        elif post_type == "meta_event":
            meta_type = data.get("meta_event_type", "")
            logger.debug(f"Parsed dict event: meta.{meta_type}")
            return Event(
                type=f"meta.{meta_type}",
                raw=data,
                user_id=0,
                raw_event_json=serialize_raw_event(data),
            )
        logger.debug(f"Unknown dict post_type={post_type!r}, discarding")
        return None

    @staticmethod
    def _parse_notice_event(raw_event: Any) -> Event:
        notice_type = getattr(raw_event, "notice_type", "")
        user_id = int(getattr(raw_event, "user_id", 0) or 0)
        group_id = getattr(raw_event, "group_id", None)
        return Event(
            type=f"notice.{notice_type}",
            raw=raw_event,
            user_id=user_id,
            group_id=int(group_id) if group_id is not None else None,
            is_group=group_id is not None,
            raw_event_json=serialize_raw_event(raw_event),
        )

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def recover_unprojected(self, bot: Bot, limit: int = 1000) -> int:
        """Replay journaled events whose message projection did not finish."""
        recovered = 0
        for row in bot.msg_store.unprojected_events(limit=limit):
            try:
                raw_event = json.loads(row["raw_json"])
                event = self.parse(raw_event)
                if event is None:
                    logger.warning(
                        f"Cannot replay journal event {row['event_id']}: unsupported type"
                    )
                    continue
                event.ingestion_source = row["source"]
                event.raw_event_json = row["raw_json"]
                bot.msg_store.record(event, trace_id=row.get("trace_id", ""))
                recovered += 1
            except Exception:
                logger.opt(exception=True).error(
                    f"Failed to replay journal event {row['event_id']}"
                )
        if recovered:
            logger.info(f"Recovered {recovered} unprojected event(s)")
        return recovered

    async def dispatch(self, raw_event: Any, bot: Bot) -> None:
        """Parse *raw_event* and route through the message bus.

        The flow is:

        1. Parse the raw event into an internal :class:`Event`.
        2. Wrap it in a ``BusMessage(EXTERNAL)`` and dispatch through
           the bus (plugins run in priority order).
        3. Flush the bus queue to process any ACTION / INTERNAL messages
           emitted by plugins.
        """
        event = self.parse(raw_event)
        if event is None:
            bot.msg_store.record_raw_event(raw_event, event_type="unhandled")
            logger.debug(
                f"Event parse returned None, discarding: {type(raw_event).__name__}"
            )
            return

        # Create the request envelope before persistence so the same trace ID is
        # stored in the Event Journal and carried through plugin dispatch.
        msg = BusMessage(
            type=MessageType.EXTERNAL,
            payload=event,
            source="event_bus",
        )
        bot.msg_store.record(event, trace_id=msg.trace_id)
        if event.message_id is not None:
            discover = getattr(bot, "discover_message_artifacts", None)
            if discover is not None:
                discover(event.message_id)
            elif getattr(bot, "artifact_store", None):
                bot.artifact_store.discover_message(event.message_id)

        extra = []
        if event.group_id:
            extra.append(f"group={event.group_id}")
        msg_preview = event.message[:200] if event.message else ""
        extra.append(f"msg='{msg_preview}'")
        logger.info(
            f"REQUEST START: {event.type} user={event.user_id} {' '.join(extra)}"
        )

        # Route only after durable event and message projection commits.
        consumed = await bot.message_bus.dispatch(msg, bot)

        # Flush queued messages (ACTION / INTERNAL emitted by plugins).
        await bot.message_bus.flush(bot)

        logger.info(
            f"REQUEST END: type={event.type} user={event.user_id} consumed={bool(consumed)}"
        )
