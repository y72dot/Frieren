"""Decorator-based plugin creation.

Each decorator wraps an async handler function and creates an anonymous
Plugin instance stored in ``func.__plugin__``.  The ``PluginManager``
later discovers these attributes during ``auto_discover()``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from src.plugin.base import Event, Plugin

if TYPE_CHECKING:
    from src.core.bot import Bot

# Type alias for decorated handler functions.
Handler = Callable[..., bool]


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


def _build_command_plugin(
    func: Handler,
    commands: list[str],
    *,
    priority: int,
) -> Plugin:
    class _CommandPlugin:
        def match(self, event: Event) -> bool:
            msg = event.message.strip()
            for cmd in commands:
                if msg == cmd or msg.startswith(cmd + " ") or msg.startswith(cmd + "\n"):
                    return True
            return False

        async def handle(self, event: Event, bot: Bot) -> bool:
            return await func(event, bot)

    _CommandPlugin.name = func.__name__
    _CommandPlugin.priority = priority
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
    func: Handler,
    pattern: str,
    *,
    priority: int,
) -> Plugin:
    compiled = re.compile(pattern)

    class _RegexPlugin:
        def match(self, event: Event) -> bool:
            return compiled.search(event.message) is not None

        async def handle(self, event: Event, bot: Bot) -> bool:
            match = compiled.search(event.message)
            if match is not None:
                return await func(event, bot, match)
            return False

    _RegexPlugin.name = func.__name__
    _RegexPlugin.priority = priority
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
    func: Handler,
    keywords: list[str],
    *,
    priority: int,
) -> Plugin:
    class _KeywordPlugin:
        def match(self, event: Event) -> bool:
            return any(kw in event.message for kw in keywords)

        async def handle(self, event: Event, bot: Bot) -> bool:
            return await func(event, bot)

    _KeywordPlugin.name = func.__name__
    _KeywordPlugin.priority = priority
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
    func: Handler,
    notice_type: str,
    *,
    priority: int,
) -> Plugin:
    target = f"notice.{notice_type}"

    class _NoticePlugin:
        def match(self, event: Event) -> bool:
            return event.type == target

        async def handle(self, event: Event, bot: Bot) -> bool:
            return await func(event, bot)

    _NoticePlugin.name = func.__name__
    _NoticePlugin.priority = priority
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
