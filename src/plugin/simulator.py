"""Offline plugin simulator for testing without a running bot or NapCat.

Usage::

    sim = Simulator(runtime)
    sim.load("my_plugin")
    result = await sim.send_group_msg(group_id=123, user_id=456, text="/hello")
    assert result["handled"] is True
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.plugin.base import Event

if TYPE_CHECKING:
    from src.plugin.runtime import PluginRuntime


# ---------------------------------------------------------------------------
# FakeEvent
# ---------------------------------------------------------------------------


def make_fake_event(
    *,
    event_type: str = "message.group",
    user_id: int = 1000,
    group_id: int | None = 123,
    message: str = "",
    message_id: int = 1,
    raw: dict | None = None,
) -> Event:
    """Build a minimal :class:`Event` suitable for offline testing."""
    raw = raw or {}
    raw.setdefault("message_type", "group" if group_id else "private")
    raw.setdefault("user_id", user_id)
    raw.setdefault("message_id", message_id)
    if group_id:
        raw.setdefault("group_id", group_id)
    raw.setdefault("message", message)
    raw.setdefault("raw_message", message)
    raw.setdefault("time", int(time.time()))

    return Event(
        type=event_type,
        user_id=user_id,
        group_id=group_id,
        message=message,
        message_id=message_id,
        raw=raw,
        raw_event_json="",
        message_array=[],
        raw_message=message,
        is_group=group_id is not None,
    )


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


@dataclass
class Simulator:
    """Offline plugin testing harness.

    Captures replies and actions for assertions without needing NapCat
    or a real QQ account.
    """

    runtime: PluginRuntime
    replies: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)

    async def load(self, plugin_id: str) -> None:
        """Activate all discovered plugins (idempotent wrapper)."""
        await self.runtime.activate(
            plugin_dirs=["plugins"],
            disabled=[],
        )

    async def send_group_msg(
        self,
        group_id: int = 123,
        user_id: int = 1000,
        text: str = "",
    ) -> dict[str, Any]:
        """Simulate a group message and dispatch it through the runtime.

        Returns a dict with ``handled``, ``replies``, and ``actions`` keys.
        """
        event = make_fake_event(
            event_type="message.group",
            group_id=group_id,
            user_id=user_id,
            message=text,
        )
        return await self._dispatch(event)

    async def send_private_msg(
        self,
        user_id: int = 1000,
        text: str = "",
    ) -> dict[str, Any]:
        """Simulate a private message dispatch."""
        event = make_fake_event(
            event_type="message.private",
            group_id=None,
            user_id=user_id,
            message=text,
        )
        return await self._dispatch(event)

    async def send_notice(
        self,
        notice_type: str,
        sub_type: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Simulate a notice event."""
        raw = {
            "notice_type": notice_type,
            "sub_type": sub_type,
            **kwargs,
        }
        event = make_fake_event(
            event_type=f"notice.{notice_type}",
            user_id=kwargs.get("user_id", 1000),
            group_id=kwargs.get("group_id"),
            message="",
            raw=raw,
        )
        return await self._dispatch(event)

    async def _dispatch(self, event: Event) -> dict[str, Any]:
        """Run the event through the bot's message pipeline."""
        self.replies.clear()
        self.actions.clear()

        from src.core.message_bus import BusMessage, MessageType

        msg = BusMessage(
            type=MessageType.EXTERNAL,
            payload=event,
            source="simulator",
        )

        handled = False
        try:
            result = await self.runtime._bus.dispatch(msg, self.runtime._bot)
            if result is True:
                handled = True
        except Exception:
            pass

        return {
            "handled": handled,
            "replies": list(self.replies),
            "actions": list(self.actions),
        }
