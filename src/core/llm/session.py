"""Session manager – per-conversation message buffers with context injection."""

from __future__ import annotations

from loguru import logger


class SessionManager:
    """Manages conversation history per session key.

    Each session is a list of dicts in OpenAI message format:
    ``{"role": "user"|"assistant"|"tool", "content": "..."}``.

    Context entries (injected by the memory plugin) are stored separately
    and rendered inline before returning messages.
    """

    def __init__(self, max_messages: int = 50) -> None:
        self._max = max_messages
        self._sessions: dict[str, list[dict]] = {}
        self._contexts: dict[str, list[dict]] = {}

    async def add_message(self, session_key: str, role: str, content: str) -> None:
        """Append a user / assistant / tool message to the session."""
        if session_key not in self._sessions:
            self._sessions[session_key] = []
        self._sessions[session_key].append({"role": role, "content": content})
        self._trim(session_key)
        logger.debug(f"Session [{session_key}] add {role}: {content[:60]}...")

    async def add_message_raw(self, session_key: str, msg: dict) -> None:
        """Append a pre-constructed message dict (e.g. assistant with tool_calls or tool result)."""
        if session_key not in self._sessions:
            self._sessions[session_key] = []
        self._sessions[session_key].append(msg)
        self._trim(session_key)
        logger.debug(f"Session [{session_key}] add raw {msg.get('role')}: {str(msg)[:80]}...")

    async def add_context(self, session_key: str, context_type: str, content: str) -> None:
        """Inject system-level context (e.g. recent chat history).

        Context is rendered as a system message at the front of the
        message list but is not counted as a conversation turn.
        """
        if session_key not in self._contexts:
            self._contexts[session_key] = []
        # Replace existing context of the same type
        self._contexts[session_key] = [
            c for c in self._contexts[session_key] if c.get("type") != context_type
        ]
        self._contexts[session_key].append({"type": context_type, "content": content})
        logger.debug(f"Session [{session_key}] context '{context_type}': {content[:60]}...")

    async def get_messages(self, session_key: str) -> list[dict]:
        """Return the full message list for a session (context + conversation)."""
        messages: list[dict] = []

        # Render context as system messages
        for ctx in self._contexts.get(session_key, []):
            messages.append({"role": "system", "content": ctx["content"]})

        messages.extend(self._sessions.get(session_key, []))
        return messages

    def clear(self, session_key: str) -> None:
        """Clear a session's messages (but keep context)."""
        self._sessions.pop(session_key, None)
        logger.debug(f"Session [{session_key}] cleared")

    def _trim(self, session_key: str) -> None:
        msgs = self._sessions.get(session_key, [])
        if len(msgs) > self._max:
            # Keep system + most recent messages, trim oldest user/assistant first
            excess = len(msgs) - self._max
            self._sessions[session_key] = msgs[excess:]
            logger.debug(f"Session [{session_key}] trimmed {excess} message(s)")
