from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.message_bus import BusMessage, MessageType  # noqa: F401  – re-export


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
