"""Risk levels for tool execution classification."""

from enum import Enum


class RiskLevel(Enum):
    """Tool risk classification for permission and audit purposes."""

    READ_ONLY = "read_only"        # No side effects: get_current_time, query_history, etc.
    WRITE = "write"                # Sends messages, sets reactions, etc.
    DESTRUCTIVE = "destructive"    # Mutes, kicks, deletes, bans, admin changes
