"""Adapter classes bridging new Spec types to the MessageBus Plugin protocol.

These adapters wrap :class:`CommandSpec`, :class:`EventHandlerSpec`,
:class:`ObserverSpec`, :class:`InternalHandlerSpec`, and
:class:`MiddlewarePipeline` so they conform to the ``match/handle``
interface expected by :class:`MessageBus` subscribers.

They are **not** legacy code — they are the architecture bridge between
the Spec-based plugin system and the bus's handler protocol.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.plugin.middleware import MiddlewarePipeline

_CQ_STRIP = re.compile(r"\[CQ:[^\]]+\]")


# ---------------------------------------------------------------------------
# Spec adapters
# ---------------------------------------------------------------------------


class _CommandSpecAdapter:
    """Adapts a :class:`CommandSpec` to the bus handler protocol."""

    def __init__(self, spec, plugin_context=None) -> None:
        self._spec = spec
        self._ctx = plugin_context
        self.name = spec.name
        self.priority = spec.priority

    def match(self, event) -> bool:
        msg = _CQ_STRIP.sub("", event.message).strip()
        names = (self._spec.name,) + self._spec.aliases
        for cmd in names:
            if msg == cmd or msg.startswith(cmd + " ") or msg.startswith(cmd + "\n"):
                return True
        return False

    async def handle(self, event, bot) -> bool:
        result = await self._invoke_handler(event, bot)
        return self._normalize_result(result)

    async def _invoke_handler(self, event, bot):
        if self._ctx is not None:
            return await self._spec.handler(event, self._ctx)
        return await self._spec.handler(event, bot)

    @staticmethod
    def _normalize_result(result) -> bool:
        from src.plugin.definition import EventResult

        if isinstance(result, EventResult):
            return result.to_bool()
        if isinstance(result, bool):
            return result
        return False


class _EventSpecAdapter:
    """Adapts an :class:`EventHandlerSpec` to the bus handler protocol."""

    def __init__(self, spec, plugin_context=None) -> None:
        self._spec = spec
        self._ctx = plugin_context
        self.name = f"{spec.event_type}:{spec.priority}"
        self.priority = spec.priority

    def match(self, event) -> bool:
        if self._spec.event_type == "*":
            return True
        return event.type == self._spec.event_type

    async def handle(self, event, bot) -> bool:
        result = await self._invoke_handler(event, bot)
        return self._normalize_result(result)

    async def _invoke_handler(self, event, bot):
        if self._ctx is not None:
            return await self._spec.handler(event, self._ctx)
        return await self._spec.handler(event, bot)

    @staticmethod
    def _normalize_result(result) -> bool:
        from src.plugin.definition import EventResult

        if isinstance(result, EventResult):
            return result.to_bool()
        if isinstance(result, bool):
            return result
        return False


class _ObserverSpecAdapter:
    """Adapts an :class:`ObserverSpec` – always matches, never consumes."""

    def __init__(self, spec, plugin_context=None) -> None:
        self._spec = spec
        self._ctx = plugin_context
        self.name = f"obs:{spec.event_type}"
        self.priority = 100  # late observer

    def match(self, event) -> bool:
        if self._spec.event_type == "*":
            return True
        return event.type == self._spec.event_type

    async def handle(self, event, bot) -> bool:
        if self._ctx is not None:
            await self._spec.handler(event, self._ctx)
        else:
            await self._spec.handler(event, bot)
        return False  # observers never consume


class _InternalSpecAdapter:
    """Adapts an :class:`InternalHandlerSpec` to the bus handler protocol."""

    def __init__(self, spec, plugin_context=None) -> None:
        self._spec = spec
        self._ctx = plugin_context
        self.name = f"int:{spec.topic}" if spec.topic else "internal_handler"
        self.priority = 0

    def match(self, payload) -> bool:
        if self._spec.topic:
            return isinstance(payload, dict) and payload.get("topic") == self._spec.topic
        return True

    async def handle(self, payload, bot) -> bool:
        result = await self._invoke_handler(payload, bot)
        return self._normalize_result(result)

    async def _invoke_handler(self, payload, bot):
        if self._ctx is not None:
            return await self._spec.handler(payload, self._ctx)
        return await self._spec.handler(payload, bot)

    @staticmethod
    def _normalize_result(result) -> bool:
        from src.plugin.definition import EventResult

        if isinstance(result, EventResult):
            return result.to_bool()
        if isinstance(result, bool):
            return result
        return False


# ---------------------------------------------------------------------------
# Legacy SubscribeAdapter – simple callable wrapper for test backward compat
# ---------------------------------------------------------------------------


class _SubscribeAdapter:
    """Adapts a plain callable ``(payload, bot) → bool`` to the bus handler protocol.

    Used in E2E test fixtures and legacy setups that don't use Spec types.
    """

    def __init__(self, func, name: str, priority: int) -> None:
        self._func = func
        self.name = name
        self.priority = priority

    def match(self, payload) -> bool:
        return True

    async def handle(self, payload, bot) -> bool:
        return await self._func(payload, bot)


# ---------------------------------------------------------------------------
# Middleware pipeline adapter
# ---------------------------------------------------------------------------


class _MiddlewarePipelineAdapter:
    """Wraps a :class:`MiddlewarePipeline` as a bus handler protocol handler.

    Registered at priority 0 on the ACTION message type so it runs
    before other ACTION handlers.  When the pipeline returns a dict
    result (truthy), the MessageBus dispatch loop stops.
    """

    # The pipeline includes the terminal QQ executor. Once it matches, the
    # ACTION has been handled even when NapCat returns an empty data payload.
    consumes_on_match = True

    def __init__(self, pipeline: MiddlewarePipeline, name: str = "action_pipeline") -> None:
        self._pipeline = pipeline
        self.name = name
        self.priority = 0

    def match(self, payload) -> bool:
        return isinstance(payload, dict) and "action" in payload

    async def handle(self, payload: dict, bot) -> dict:
        action = payload.get("action", "")
        params = {k: v for k, v in payload.items() if k not in ("action", "_qqbot_quiet")}
        result = await self._pipeline.execute(action, params)
        return result  # dict is truthy → MessageBus stops dispatch chain
