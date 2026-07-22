"""Per-session LLM conversation logger – human-readable structured logs."""

from __future__ import annotations

import json as _json
import time
from contextlib import suppress
from pathlib import Path

from loguru import logger


def _short_id(call_id: str) -> str:
    """Shorten ``call_00_1qWYoDtiEJ1YDc0j3SMp7759`` → ``call_00``."""
    parts = call_id.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else call_id


def _fmt_args(args: dict) -> str:
    """Format tool call arguments for human-readable log output.

    Simple dict (str/int/float/bool/None values) → ``key=val, key2=val2``
    Empty dict → ``()``, otherwise JSON fallback.
    """
    if not args:
        return "()"
    if isinstance(args, dict) and all(
        isinstance(v, (str, int, float, bool, type(None))) for v in args.values()
    ):
        return f"({', '.join(f'{k}={v!r}' for k, v in args.items())})"
    return f"({_json.dumps(args, ensure_ascii=False)})"


class LlmSessionLogger:
    """Writes per-session LLM conversation logs to ``logs/llm_sessions/``.

    Each session gets its own file.  Uses a loguru sink with a session-key
    filter so that all log calls for the same session end up in the same file.
    """

    _sinks: dict[str, int] = {}

    def __init__(self, session_key: str) -> None:
        self._session_key = session_key

        # Sanitize session key for filesystem: "group:643998265" → "group_643998265"
        safe = session_key.replace(":", "_").replace("/", "_").replace("\\", "_")
        self._file_name = f"{safe}.log"

        log_dir = Path("logs/llm_sessions")
        log_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = log_dir / self._file_name

        if session_key not in self._sinks:
            sid = logger.add(
                str(self._file_path),
                format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
                filter=lambda record, key=session_key: record["extra"].get("session") == key,  # type: ignore[misc]
                enqueue=True,
                encoding="utf-8",
            )
            self._sinks[session_key] = sid

        self._log = logger.bind(session=session_key)
        self._start_time = time.time()
        self._last_msg_count = 0
        self._current_turn = 0
        self._tool_call_count = 0

    # -- public API ----------------------------------------------------------

    def trigger(self, nickname: str, text: str, is_group: bool) -> None:
        label = self._session_key
        self._log.info(f"==== SESSION START ({label}) ====")
        label = "群聊" if is_group else "私聊"
        self._log.info(f"[TRIGGER] {label} {nickname}: {text}")

    def session_reuse(self, msg_count: int, age: float) -> None:
        self._log.info(f"[REUSE] {msg_count} messages, age={age:.0f}s")

    def session_new(self, msg_count: int) -> None:
        self._log.info(f"[NEW] {msg_count} messages")

    def turn_start(self, turn: int, max_turns: int) -> None:
        self._current_turn = turn
        self._log.info(f"--- Turn {turn}/{max_turns} ---")

    def request(self, messages: list[dict], model: str, tool_count: int) -> None:
        self._log.info(
            f"REQ model={model} msgs={len(messages)} tools={tool_count}"
        )
        if self._current_turn > 1 and self._last_msg_count > 0:
            skip = self._last_msg_count
            self._log.info(f"  ... (前 {skip} 条消息同上轮)")
            msgs_to_print = messages[skip:]
        else:
            msgs_to_print = messages

        for msg in msgs_to_print:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")

            if role == "system":
                label = "SYSTEM "
            elif role == "user":
                label = "USER   "
            elif role == "assistant":
                label = "ASSIST "
                if tool_calls:
                    label = "ASSIST(tool)"
            elif role == "tool":
                label = "TOOL   "
            else:
                label = f"{role:<7}"

            if tool_calls:
                self._log.info(
                    f"  [{label}] tool_calls={len(tool_calls)}"
                )
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    self._log.info(
                        f"    {_short_id(tc.get('id','?'))}: {fn.get('name','?')}({args})"
                    )
            elif content:
                preview = str(content)[:200]
                self._log.info(f"  [{label}] {preview}")
            else:
                self._log.info(f"  [{label}] <empty>")

        self._last_msg_count = len(messages)

    def tool_view(self, names: tuple[str, ...], packs: tuple[str, ...]) -> None:
        """Record the exact per-turn tools exposed to the model."""
        self._log.info(
            f"TOOLS count={len(names)} packs={','.join(packs)} "
            f"names={','.join(names)}"
        )

    def text_response(self, text: str) -> None:
        if not text.strip():
            self._log.info("RES text=(empty)")
            return
        preview = text[:200]
        self._log.info(f"RES text={len(text)}chars")
        self._log.info(f"  [TEXT   ] {preview}")

    def tool_calls_result(self, tool_calls: list) -> None:
        self._tool_call_count += len(tool_calls)
        self._log.info(f"RES tool_calls={len(tool_calls)}")
        for tc in tool_calls:
            if isinstance(tc, dict):
                call_id = tc.get("id", "?")
                name = tc.get("name", "?")
                args = tc.get("arguments", {})
            else:
                call_id = tc.id
                name = tc.name
                args = tc.arguments
            if isinstance(args, str):
                with suppress(_json.JSONDecodeError, TypeError):
                    args = _json.loads(args)
            self._log.info(f"  TCALL {_short_id(call_id)} {name}{_fmt_args(args)}")

    def tool_result(self, call_id: str, name: str, result: str) -> None:
        preview = str(result)
        self._log.info(f"  TRES  {_short_id(call_id)} {name}: {preview}")

    def final_text(self, text: str) -> None:
        preview = text[:200]
        self._log.info(f"SEND {preview}")

    def max_turns_forced(self) -> None:
        self._log.warning("MAX_TURNS reached, forcing final reply without tools")

    def session_end(self, turns: int) -> None:
        elapsed = time.time() - self._start_time
        self._log.info(f"==== SESSION END {turns} turns, {elapsed:.2f}s ====")

        # Emit a summary line to bot.log for cross-file correlation
        logger.info(
            f"LLM session summary: session={self._session_key} "
            f"turns={turns} tool_calls={self._tool_call_count} elapsed={elapsed:.1f}s"
        )

        # Remove the sink for this session to prevent memory leak
        sid = self._sinks.pop(self._session_key, None)
        if sid is not None:
            with suppress(ValueError):
                logger.remove(sid)
