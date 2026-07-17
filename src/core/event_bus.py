"""Convert napcat-sdk events into internal Events and route them."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.plugin.base import Event

if TYPE_CHECKING:
    from src.core.bot import Bot


# Try importing napcat event types – they may not be installed during tests.
try:
    from napcat import (  # type: ignore[import-untyped]
        GroupMessageEvent,
        PrivateMessageEvent,
    )

    _NAPCAT_AVAILABLE = True
except ImportError:  # pragma: no cover
    GroupMessageEvent: Any = None  # type: ignore[no-redef]
    PrivateMessageEvent: Any = None  # type: ignore[no-redef]
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
    and dispatches them to plugins and event listeners."""

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
                message=raw_event.raw_message or "",
                group_id=int(raw_event.group_id),
                is_group=True,
            )

        if PrivateMessageEvent is not None and isinstance(raw_event, PrivateMessageEvent):
            return Event(
                type="message.private",
                raw=raw_event,
                user_id=int(raw_event.user_id),
                message=raw_event.raw_message or "",
                is_group=False,
            )

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
            return Event(
                type=f"message.{msg_type}" if msg_type else "message",
                raw=data,
                user_id=int(data.get("user_id", 0)),
                message=str(data.get("raw_message", data.get("message", ""))),
                group_id=int(group_id) if group_id is not None else None,
                is_group=msg_type == "group",
            )
        elif post_type == "notice":
            notice_type = data.get("notice_type", "")
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
            return Event(
                type=f"request.{req_type}",
                raw=data,
                user_id=int(data.get("user_id", 0)),
                message=str(data.get("comment", "")),
                group_id=int(gid) if (gid := data.get("group_id")) is not None else None,
                is_group=gid is not None,
            )
        elif post_type == "meta_event":
            meta_type = data.get("meta_event_type", "")
            return Event(
                type=f"meta.{meta_type}",
                raw=data,
                user_id=0,
            )
        return None

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, raw_event: Any, bot: Bot) -> None:
        """Parse *raw_event* and route to plugin manager or listeners."""
        event = self.parse(raw_event)
        if event is None:
            return

        logger.debug(f"Event: {event.type} from user {event.user_id}")

        if event.type in ("message.group", "message.private"):
            consumed = await bot.plugin_manager.dispatch(event, bot)
            if consumed:
                logger.debug(f"Event consumed by plugin: {event.type}")
            return

        # Non-message events → emit to listeners
        parts = event.type.split(".", 1)
        if parts:
            await self._emit(parts[0], event, bot)
