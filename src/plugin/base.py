from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.core.bot import Bot


@dataclass
class Event:
    """Internal event representation, decoupled from napcat-sdk types."""

    type: str
    """Event type string, e.g. ``"message.group"``, ``"notice.group_increase"``."""

    user_id: int
    """QQ ID of the message sender or event subject."""

    raw: Any = None
    """The original napcat-sdk event object."""

    message: str = ""
    """Plain-text message content (``raw_message``)."""

    group_id: int | None = None
    """Group ID if this is a group event."""

    is_group: bool = False
    """Convenience flag for group vs. private context."""


@runtime_checkable
class Plugin(Protocol):
    """Protocol that every plugin must satisfy.

    Plugins do **not** need to inherit from this class – any object with the
    matching attributes and methods will pass :func:`isinstance` checks thanks
    to :func:`runtime_checkable`.
    """

    name: str
    """Unique plugin name (used for disable lists and logging)."""

    priority: int
    """Lower values are matched first."""

    def match(self, event: Event) -> bool:
        """Return ``True`` if this plugin wants to handle *event*."""
        ...

    async def handle(self, event: Event, bot: Bot) -> bool:
        """Handle the event.

        Returns
        -------
        bool
            ``True`` if the event was consumed (stop further matching),
            ``False`` to continue trying other plugins.
        """
        ...
