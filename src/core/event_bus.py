"""Convert napcat-sdk events into internal Events and route them."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

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


# Async listener type — sync listeners are wrapped in _normalise().
Listener = Callable[[Event, "Bot"], Awaitable[Any]]


def _normalise(callback: Callable[[Event, Bot], Any]) -> Listener:
    """Wrap a synchronous callback so it can be awaited.

    If *callback* is already a coroutine function, it is returned as-is.
    """
    if inspect.iscoroutinefunction(callback):
        return callback  # type: ignore[return-value]

    async def _wrapper(event: Event, bot: Bot) -> Any:
        return callback(event, bot)

    _wrapper.__name__ = callback.__name__
    _wrapper.__wrapped__ = callback  # type: ignore[attr-defined]
    return _wrapper


class EventBus:
    """Parses raw napcat-sdk events into internal :class:`Event` objects
    and dispatches them through the :class:`MessageBus`."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = {}

    # ------------------------------------------------------------------
    # listener management (Phase 2+)
    # ------------------------------------------------------------------

    def on(self, event_prefix: str, callback: Callable[[Event, Bot], Any]) -> None:
        """Register a listener for events whose type starts with *event_prefix*.

        Synchronous callbacks are wrapped so every registered listener is
        await-able, avoiding per-invocation :func:`inspect` overhead.
        """
        async_cb = _normalise(callback)
        self._listeners.setdefault(event_prefix, []).append(async_cb)

    def off(self, event_prefix: str, callback: Callable[[Event, Bot], Any]) -> None:
        """Remove a previously registered listener."""
        lst = self._listeners.get(event_prefix)
        if lst is None:
            return
        # Remove the wrapped version if it matches
        for wrapped in list(lst):
            if getattr(wrapped, "__wrapped__", None) is callback:
                lst.remove(wrapped)
                break
        if callback in lst:
            lst.remove(callback)
        if not lst:
            del self._listeners[event_prefix]

    async def _emit(self, event_prefix: str, event: Event, bot: Bot) -> None:
        """Call all listeners matching *event_prefix*."""
        for prefix, listeners in self._listeners.items():
            if event.type.startswith(prefix):
                for cb in listeners:
                    try:
                        await cb(event, bot)
                    except Exception:
                        logger.opt(exception=True).error(
                            f"Listener {cb.__name__!r} raised an exception"
                        )

    # ------------------------------------------------------------------
    # parsing
    # ------------------------------------------------------------------

    def parse(self, raw_event: Any) -> Event | None:
        """Convert a napcat-sdk event to an internal :class:`Event`.

        Returns ``None`` for unknown / unhandled event types.
        """
        # typed message events (napcat-sdk >= 0.1)
        if GroupMessageEvent is not None and isinstance(raw_event, GroupMessageEvent):
            return Event(
                type="message.group",
                raw=raw_event,
                user_id=int(raw_event.user_id),
                message_id=int(raw_event.message_id),
                message=raw_event.raw_message or "",
                group_id=int(raw_event.group_id),
                is_group=True,
            )

        if PrivateMessageEvent is not None and isinstance(
            raw_event, PrivateMessageEvent
        ):
            return Event(
                type="message.private",
                raw=raw_event,
                user_id=int(raw_event.user_id),
                message_id=int(raw_event.message_id),
                message=raw_event.raw_message or "",
                is_group=False,
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
            logger.debug(
                f"Parsed dict event: message.{msg_type}" if msg_type else "message"
            )
            return Event(
                type=f"message.{msg_type}" if msg_type else "message",
                raw=data,
                user_id=int(data.get("user_id", 0)),
                message_id=int(data.get("message_id", 0)) or None,
                message=str(data.get("raw_message", data.get("message", ""))),
                group_id=int(group_id) if group_id is not None else None,
                is_group=msg_type == "group",
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
            )
        elif post_type == "meta_event":
            meta_type = data.get("meta_event_type", "")
            logger.debug(f"Parsed dict event: meta.{meta_type}")
            return Event(
                type=f"meta.{meta_type}",
                raw=data,
                user_id=0,
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
        )

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, raw_event: Any, bot: Bot) -> None:
        """Parse *raw_event* and route through the message bus.

        The flow is:

        1. Parse the raw event into an internal :class:`Event`.
        2. Wrap it in a ``BusMessage(EXTERNAL)`` and dispatch through
           the bus (plugins run in priority order).
        3. Flush the bus queue to process any ACTION / INTERNAL messages
           emitted by plugins.
        4. Fall back to legacy listeners if no plugin consumed the event.
        """
        event = self.parse(raw_event)
        if event is None:
            logger.debug(
                f"Event parse returned None, discarding: {type(raw_event).__name__}"
            )
            return

        # Record message in the persistent store (before dispatch so plugins can query it).
        bot.msg_store.record(event)

        extra = []
        if event.group_id:
            extra.append(f"group={event.group_id}")
        msg_preview = event.message[:200] if event.message else ""
        extra.append(f"msg='{msg_preview}'")
        logger.debug(f"Event: {event.type} user={event.user_id} {' '.join(extra)}")

        # Phase 2: route through the message bus.
        msg = BusMessage(
            type=MessageType.EXTERNAL,
            payload=event,
            source="event_bus",
        )
        consumed = await bot.message_bus.dispatch(msg, bot)

        # Flush queued messages (ACTION / INTERNAL emitted by plugins).
        await bot.message_bus.flush(bot)

        if consumed:
            logger.debug(f"Event consumed by bus: {event.type}")
            return

        # Legacy fallback: emit to raw listeners.
        logger.debug(
            f"Event not consumed, falling back to legacy listeners: {event.type}"
        )
        parts = event.type.split(".", 1)
        if parts:
            await self._emit(parts[0], event, bot)
