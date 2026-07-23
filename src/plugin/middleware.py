"""ActionMiddleware protocol and MiddlewarePipeline.

Provides a middleware chain for ACTION messages that wraps the terminal
API executor.  Each middleware can inspect, modify, or block actions
before they reach the terminal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

CallNext = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class ActionMiddleware(Protocol):
    """Middleware for ACTION messages.

    Each middleware can inspect/modify the action and params before
    passing to the next link, or block entirely by returning a dict
    without calling ``call_next``.
    """

    name: str
    priority: int

    async def process(
        self, action: str, params: dict[str, Any], call_next: CallNext
    ) -> dict[str, Any]: ...


class MiddlewarePipeline:
    """Ordered middleware chain with a terminal executor.

    Builds the chain from the inside out:
    the highest-priority middleware wraps the terminal,
    each subsequent middleware wraps the previous one.
    """

    def __init__(
        self,
        middlewares: list[ActionMiddleware],
        terminal: CallNext,
    ) -> None:
        # Sort by priority ascending (lower = outer, runs first).
        self._middlewares = sorted(middlewares, key=lambda m: m.priority)
        self._terminal = terminal

    async def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        handler = self._build_chain(0)
        return await handler(action, params)

    def _build_chain(self, index: int) -> CallNext:
        if index >= len(self._middlewares):
            return self._terminal
        mw = self._middlewares[index]
        next_link = self._build_chain(index + 1)

        async def wrapper(action: str, params: dict[str, Any]) -> dict[str, Any]:
            return await mw.process(action, params, next_link)

        return wrapper

    @property
    def middleware_names(self) -> list[str]:
        return [mw.name for mw in self._middlewares]
