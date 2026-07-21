"""Circuit breaker for the LLM agent loop."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class CircuitBreaker:
    """Prevents runaway agent loops by tracking consecutive errors and
    repeated identical tool calls."""

    def __init__(
        self,
        max_consecutive_errors: int = 3,
        max_same_tool_repeats: int = 5,
    ) -> None:
        self.max_consecutive_errors = max_consecutive_errors
        self.max_same_tool_repeats = max_same_tool_repeats
        self._error_count = 0
        self._tool_call_history: dict[str, int] = {}  # name+args_hash -> count

    # ------------------------------------------------------------------
    # recording
    # ------------------------------------------------------------------

    def record_error(self, tool_name: str, error: str) -> bool:
        """Record a tool execution error. Returns True if the breaker trips."""
        self._error_count += 1
        return self._error_count >= self.max_consecutive_errors

    def record_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Record a tool call. Returns True if this exact call has been
        repeated more than *max_same_tool_repeats* times."""
        key = _make_tool_key(tool_name, args)
        count = self._tool_call_history.get(key, 0) + 1
        self._tool_call_history[key] = count
        return count > self.max_same_tool_repeats

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    @property
    def is_tripped(self) -> bool:
        """True if the circuit breaker has been tripped."""
        return self._error_count >= self.max_consecutive_errors

    def reset(self) -> None:
        """Reset all counters (e.g. on new session)."""
        self._error_count = 0
        self._tool_call_history.clear()


def _make_tool_key(name: str, args: dict[str, Any]) -> str:
    raw = json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()
