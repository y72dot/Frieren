"""Decorator-based plugin creation.

Each decorator wraps an async handler function and creates an anonymous
Plugin instance stored in ``func.__plugin__``.  The ``PluginManager``
later discovers these attributes during ``auto_discover()`` and
subscribes them to the :class:`MessageBus`.

New in Phase 2: the ``@subscribe`` decorator registers a handler for a
specific :class:`MessageType` on the bus directly.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from src.plugin.base import Event, Plugin

_CQ_STRIP = re.compile(r"\[CQ:[^\]]+\]")

if TYPE_CHECKING:
    from src.core.bot import Bot
    from src.core.message_bus import MessageType

# Type alias for decorated async handler functions.
Handler = Callable[..., Awaitable[bool]]


# ---------------------------------------------------------------------------
# @subscribe – generic bus subscription
# ---------------------------------------------------------------------------


def subscribe(
    message_type: MessageType,
    *,
    priority: int = 0,
) -> Callable[[Handler], Handler]:
    """Register a handler for a specific message type on the bus.

    The handler receives ``(bus_message: BusMessage, bot: Bot)`` and
    should return ``True`` to suppress the message (for EXTERNAL /
    ACTION types) or any value for INTERNAL / LIFECYCLE types.

    Example::

        @subscribe(MessageType.INTERNAL, priority=10)
        async def on_metrics(msg: BusMessage, bot: Bot) -> bool:
            logger.info(f"Metric: {msg.payload}")
            return False
    """

    def decorator(func: Handler) -> Handler:
        func.__subscribe__ = (message_type, priority)  # type: ignore[attr-defined]
        return func

    return decorator


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


def _build_command_plugin(
    func: Callable[..., Awaitable[bool]],
    commands: list[str],
    *,
    priority: int,
) -> Plugin:
    class _CommandPlugin:
        name: str
        priority: int

        def __init__(self) -> None:
            self.name = func.__name__
            self.priority = priority

        def match(self, event: Event) -> bool:
            msg = _CQ_STRIP.sub("", event.message).strip()
            for cmd in commands:
                if (
                    msg == cmd
                    or msg.startswith(cmd + " ")
                    or msg.startswith(cmd + "\n")
                ):
                    return True
            return False

        async def handle(self, event: Event, bot: Bot) -> bool:
            return await func(event, bot)

    return _CommandPlugin()


def command(
    cmds: str | Sequence[str],
    *,
    priority: int = 0,
) -> Callable[[Handler], Handler]:
    """Register an async function as a command plugin.

    The plugin matches when *event.message* starts with one of *cmds*
    (exact match or followed by a space / newline).
    """
    if isinstance(cmds, str):
        cmds = [cmds]
    command_list = list(cmds)

    def decorator(func: Handler) -> Handler:
        func.__plugin__ = _build_command_plugin(func, command_list, priority=priority)  # type: ignore[attr-defined]
        return func

    return decorator


# ---------------------------------------------------------------------------
# on_regex
# ---------------------------------------------------------------------------


def _build_regex_plugin(
    func: Callable[..., Awaitable[bool]],
    pattern: str,
    *,
    priority: int,
) -> Plugin:
    compiled = re.compile(pattern)

    class _RegexPlugin:
        name: str
        priority: int

        def __init__(self) -> None:
            self.name = func.__name__
            self.priority = priority

        def match(self, event: Event) -> bool:
            return compiled.search(event.message) is not None

        async def handle(self, event: Event, bot: Bot) -> bool:
            match = compiled.search(event.message)
            if match is not None:
                return await func(event, bot, match)  # type: ignore[call-arg]
            return False

    return _RegexPlugin()


def on_regex(
    pattern: str,
    *,
    priority: int = 5,
) -> Callable[[Handler], Handler]:
    """Register an async function as a regex plugin.

    The handler receives an additional ``match`` argument (``re.Match``).
    """

    def decorator(func: Handler) -> Handler:
        func.__plugin__ = _build_regex_plugin(func, pattern, priority=priority)  # type: ignore[attr-defined]
        return func

    return decorator


# ---------------------------------------------------------------------------
# on_keyword
# ---------------------------------------------------------------------------


def _build_keyword_plugin(
    func: Callable[..., Awaitable[bool]],
    keywords: list[str],
    *,
    priority: int,
) -> Plugin:
    class _KeywordPlugin:
        name: str
        priority: int

        def __init__(self) -> None:
            self.name = func.__name__
            self.priority = priority

        def match(self, event: Event) -> bool:
            return any(kw in event.message for kw in keywords)

        async def handle(self, event: Event, bot: Bot) -> bool:
            return await func(event, bot)

    return _KeywordPlugin()


def on_keyword(
    keywords: str | Sequence[str],
    *,
    priority: int = 10,
) -> Callable[[Handler], Handler]:
    """Register an async function as a keyword plugin.

    Matches when any of *keywords* appears anywhere in the message.
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    kw_list = list(keywords)

    def decorator(func: Handler) -> Handler:
        func.__plugin__ = _build_keyword_plugin(func, kw_list, priority=priority)  # type: ignore[attr-defined]
        return func

    return decorator


# ---------------------------------------------------------------------------
# on_notice
# ---------------------------------------------------------------------------


def _build_notice_plugin(
    func: Callable[..., Awaitable[bool]],
    notice_type: str,
    *,
    priority: int,
) -> Plugin:
    target = f"notice.{notice_type}"

    class _NoticePlugin:
        name: str
        priority: int

        def __init__(self) -> None:
            self.name = func.__name__
            self.priority = priority

        def match(self, event: Event) -> bool:
            return event.type == target

        async def handle(self, event: Event, bot: Bot) -> bool:
            return await func(event, bot)

    return _NoticePlugin()


def on_notice(
    notice_type: str,
    *,
    priority: int = 0,
) -> Callable[[Handler], Handler]:
    """Register an async function as a notice-event plugin.

    *notice_type* is e.g. ``"group_increase"`` which maps to
    ``event.type == "notice.group_increase"``.
    """

    def decorator(func: Handler) -> Handler:
        func.__plugin__ = _build_notice_plugin(func, notice_type, priority=priority)  # type: ignore[attr-defined]
        return func

    return decorator
