"""Message bus: typed message routing with priority-based suppression.

All plugin communication flows through the bus. External events,
API calls, and inter-plugin messages are all :class:`BusMessage`
instances dispatched by type+priority.
"""

from __future__ import annotations

import sys
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from src.core.bot import Bot
    from src.plugin.base import Plugin

# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------


class MessageType(StrEnum):
    EXTERNAL = "external"  # NapCat event  → suppressible
    ACTION = "action"  # send to QQ      → suppressible
    INTERNAL = "internal"  # plugin ↔ plugin → not suppressible
    LIFECYCLE = "lifecycle"  # bot start/stop → not suppressible


@dataclass
class BusMessage:
    """Envelope for all messages flowing through the bus."""

    type: MessageType
    payload: Any
    source: str = ""
    depth: int = 0
    timestamp: float = field(default_factory=time.time)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class Subscription:
    """A handler registered for a specific message type at a given priority."""

    handler: Plugin
    priority: int
    message_type: MessageType


# ---------------------------------------------------------------------------
# built-in QQExec – final ACTION handler
# ---------------------------------------------------------------------------


class _QQExec:
    """Built-in fallback handler that executes ACTION messages against the
    real napcat API. Registered at fixed priority=100.

    Placed here (not in api_client) to avoid circular imports.
    """

    name = "_qq_exec"
    priority = 100

    def match(self, payload: Any) -> bool:
        return True

    async def handle(self, payload: Any, bot: Bot) -> Any:
        action: str = payload.get("action", "")
        if not action:
            logger.warning("ACTION message without 'action' field, dropping")
            return None
        params = {k: v for k, v in payload.items() if k != "action"}
        logger.debug(f"_QQExec: {action}")
        return await bot.api._raw_call(action, **params)


# ---------------------------------------------------------------------------
# MessageBus
# ---------------------------------------------------------------------------


class MessageBus:
    """Typed, priority-ordered message bus.

    Subscribers are registered per :class:`MessageType` and sorted by
    priority on each dispatch.

    Lifecycle
    ---------
    1. ``subscribe()`` – register a handler plugin for a message type.
    2. ``dispatch(msg, bot)`` – immediately run the handler chain.
    3. ``emit(msg)`` – enqueue a message for later processing.
    4. ``flush(bot)`` – drain the internal queue (breadth-first).
    """

    # Sentinel for the built-in ACTION executor.
    _MAX_PRIORITY = sys.maxsize

    def __init__(self) -> None:
        self._subscriptions: dict[MessageType, list[Subscription]] = {
            t: [] for t in MessageType
        }
        self._queue: list[BusMessage] = []
        self._register_builtins()

    # ------------------------------------------------------------------
    # subscription management
    # ------------------------------------------------------------------

    def subscribe(
        self,
        message_type: MessageType,
        handler: Plugin,
        priority: int,
    ) -> None:
        """Register *handler* for *message_type* at the given *priority*."""
        # Don't allow overriding the built-in QQExec.
        if handler.name == "_qq_exec":
            existing = [
                s
                for s in self._subscriptions[MessageType.ACTION]
                if s.handler.name == "_qq_exec"
            ]
            if existing:
                return

        # Avoid duplicate subscriptions by name + type.
        for s in self._subscriptions[message_type]:
            if s.handler.name == handler.name:
                logger.debug(
                    f"Duplicate subscription '{handler.name}' for {message_type.value}, replacing"
                )
                self._subscriptions[message_type].remove(s)
                break

        sub = Subscription(
            handler=handler, priority=priority, message_type=message_type
        )
        self._subscriptions[message_type].append(sub)
        logger.debug(
            f"Subscribed '{handler.name}' to {message_type.value} (priority={priority})"
        )

    def unsubscribe(self, message_type: MessageType, handler_name: str) -> bool:
        """Remove a subscription by handler name. Returns True if removed."""
        subs = self._subscriptions[message_type]
        for s in list(subs):
            if s.handler.name == handler_name:
                subs.remove(s)
                logger.debug(f"Unsubscribed '{handler_name}' from {message_type.value}")
                return True
        return False

    @property
    def subscription_count(self) -> int:
        """Total number of subscriptions across all message types."""
        return sum(len(v) for v in self._subscriptions.values())

    # ------------------------------------------------------------------
    # dispatch (immediate)
    # ------------------------------------------------------------------

    async def dispatch(self, msg: BusMessage, bot: Bot) -> Any:
        """Immediately run the handler chain for *msg*.

        Returns
        -------
        Any
            For EXTERNAL: ``True`` if consumed, ``False`` otherwise.
            For ACTION:   the return value from the last handler (e.g. API response).
            For INTERNAL / LIFECYCLE: always ``None``.
        """
        if msg.depth > 10:
            logger.warning(
                f"BusMessage depth={msg.depth} exceeds limit, dropping "
                f"(type={msg.type.value} source={msg.source} trace={msg.trace_id})"
            )
            return False

        suppressible = msg.type in (MessageType.EXTERNAL, MessageType.ACTION)

        subs = self._subscriptions.get(msg.type, [])
        ordered = sorted(subs, key=lambda s: s.priority)

        # Only EXTERNAL events set a trace_id context; nested dispatches
        # (ACTION etc.) use nullcontext to inherit the outer trace_id.
        ctx = (
            logger.contextualize(trace_id=msg.trace_id)
            if msg.type == MessageType.EXTERNAL
            else nullcontext()
        )

        with ctx:
            logger.debug(
                f"Dispatching {msg.type.value} to {len(ordered)} subscriber(s)"
            )

            # -- global filter: block the entire event before any plugin sees it --
            if msg.type == MessageType.EXTERNAL and bot.filter_mgr.is_global_blocked(
                msg.payload
            ):
                return False

            for sub in ordered:
                # -- per-plugin filter: skip this plugin only --
                if (
                    msg.type == MessageType.EXTERNAL
                    and bot.filter_mgr.is_plugin_blocked(sub.handler.name, msg.payload)
                ):
                    continue

                # match
                try:
                    matched = sub.handler.match(msg.payload)
                except Exception:
                    logger.opt(exception=True).error(
                        f"Plugin.match() raised: {sub.handler.name}"
                    )
                    continue

                logger.debug(f"'{sub.handler.name}'.match() -> {matched}")

                if not matched:
                    continue

                # handle
                try:
                    result = await sub.handler.handle(msg.payload, bot)
                except Exception:
                    logger.opt(exception=True).error(
                        f"Plugin.handle() raised: {sub.handler.name}"
                    )
                    continue

                logger.debug(f"'{sub.handler.name}'.handle() -> {bool(result)}")

                if suppressible and result:
                    logger.debug(f"Message suppressed by '{sub.handler.name}'")
                    return result

            # Non-suppressible types: run all handlers.
            if not suppressible:
                return None

            # Suppressible but not consumed.
            logger.debug(f"Event not consumed (type={msg.type.value})")
            return False

    # ------------------------------------------------------------------
    # emit (deferred / queued)
    # ------------------------------------------------------------------

    def emit(self, msg: BusMessage) -> None:
        """Enqueue *msg* for later processing via :meth:`flush`.

        Use this inside plugin handlers when you want fire-and-forget
        semantics (e.g. sending a message without waiting for the API
        response).
        """
        msg.depth += 1
        if msg.depth > 10:
            logger.warning(
                f"BusMessage depth={msg.depth} exceeds limit on emit, dropping "
                f"(type={msg.type.value} source={msg.source} trace={msg.trace_id})"
            )
            return
        self._queue.append(msg)
        logger.debug(
            f"Queued {msg.type.value} (source={msg.source} trace={msg.trace_id})"
        )

    async def emit_and_wait(self, msg: BusMessage, bot: Bot) -> Any:
        """Emit a message and wait for it to be dispatched immediately.

        This bypasses the queue for cases where the caller needs the
        result of the handler chain.
        """
        msg.depth += 1
        return await self.dispatch(msg, bot)

    # ------------------------------------------------------------------
    # flush (drain internal queue)
    # ------------------------------------------------------------------

    async def flush(self, bot: Bot, max_rounds: int = 10) -> None:
        """Drain the internal message queue breadth-first.

        Processing enqueued messages may produce new messages (e.g. an
        ACTION handler emits an INTERNAL notification).  Those are
        collected and processed in the next round, up to *max_rounds*.

        Parameters
        ----------
        bot:
            The bot instance (passed to handlers).
        max_rounds:
            Maximum number of flush rounds to prevent message storms.
        """
        for round_num in range(1, max_rounds + 1):
            if not self._queue:
                break

            batch = self._queue[:]
            self._queue = []

            logger.debug(f"Flush round {round_num}: {len(batch)} message(s)")

            for msg in batch:
                try:
                    await self.dispatch(msg, bot)
                except Exception:
                    logger.opt(exception=True).error(
                        f"Error during flush dispatch "
                        f"(type={msg.type.value} trace={msg.trace_id})"
                    )

        if self._queue:
            leftover = len(self._queue)
            logger.warning(
                f"Message queue still has {leftover} message(s) after "
                f"{max_rounds} flush rounds – truncating"
            )
            self._queue = []

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _register_builtins(self) -> None:
        """Register mandatory built-in handlers."""
        self.subscribe(MessageType.ACTION, _QQExec(), _QQExec.priority)

    def clear(self) -> None:
        """Remove all subscriptions and clear the queue (for testing)."""
        self._subscriptions = {t: [] for t in MessageType}
        self._queue = []
        self._register_builtins()
