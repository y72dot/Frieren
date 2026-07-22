from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from src.core.message_bus import BusMessage, MessageType  # noqa: F401  – re-export

if TYPE_CHECKING:
    from src.core.bot import Bot


@dataclass
class Event:
    """Internal event representation, decoupled from napcat-sdk types."""

    type: str
    """Event type string, e.g. ``"message.group"``, ``"notice.group_increase"``."""

    user_id: int
    """QQ ID of the message sender or event subject."""

    message_id: int | None = None
    """NapCat message ID (only for message events)."""

    raw: Any = None
    """The original napcat-sdk event object."""

    message: str = ""
    """Plain-text message content (``raw_message``)."""

    group_id: int | None = None
    """Group ID if this is a group event."""

    is_group: bool = False
    """Convenience flag for group vs. private context."""

    raw_message: str = ""
    """Exact NapCat ``raw_message``. Unlike ``message``, never synthesized."""

    message_array: list[dict[str, Any]] = field(default_factory=list)
    """Original NapCat message segments when provided by the event."""

    raw_event_json: str = ""
    """Lossless JSON serialization used by the Event Journal."""

    ingestion_source: str = "live"
    """Where this event came from: live, backfill, fallback, or legacy."""

    peer_id: int | None = None
    """Private-conversation peer, especially for bot-originated messages."""


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
