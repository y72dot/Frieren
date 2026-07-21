"""Unified tool execution with validation, permission, cache, and audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time as _time
from pathlib import Path
from typing import Any

from loguru import logger

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef
from src.core.llm.tool_permissions import ToolCallContext, check_permission


class ToolExecutor:
    """Execute LLM tool calls with validation, permission check, caching, and audit."""

    def __init__(
        self,
        catalog: ToolCatalog,
        default_timeout: float = 30.0,
        audit_log_path: str = "logs/audit.log",
    ) -> None:
        self.catalog = catalog
        self.default_timeout = default_timeout
        self.audit_log_path = Path(audit_log_path)
        self._result_cache: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolCallContext,
        bot,
    ) -> dict[str, Any]:
        """Execute a single tool call through the full pipeline.

        1. Look up tool definition
        2. Validate required parameters
        3. Check permissions
        4. Check cache (READ_ONLY tools with cache_ttl > 0)
        5. Execute with timeout
        6. Write cache
        7. Audit log (DESTRUCTIVE tools)
        """
        tool = self.catalog.get(tool_name)
        if tool is None:
            return {"error": f"unknown tool: {tool_name}"}

        # -- validation --
        err = _validate_args(tool, args)
        if err:
            return {"error": err}

        # -- permission --
        allowed, reason = check_permission(tool, ctx)
        if not allowed:
            logger.warning(f"Tool '{tool_name}' denied: {reason}")
            return {"error": reason}

        # -- cache --
        cache_key = ""
        if tool.cache_ttl > 0 and tool.risk_level == RiskLevel.READ_ONLY:
            cache_key = _make_cache_key(tool_name, args)
            cached = self._result_cache.get(cache_key)
            if cached is not None:
                ts, val = cached
                if _time.time() - ts < tool.cache_ttl:
                    logger.debug(f"Tool '{tool_name}' cache hit")
                    return val

        # -- execute --
        try:
            result = await asyncio.wait_for(
                tool.executor(args, ctx.group_id, ctx.user_id, bot),
                timeout=self.default_timeout,
            )
        except asyncio.TimeoutError:
            logger.error(f"Tool '{tool_name}' timed out after {self.default_timeout}s")
            return {"error": f"工具执行超时 ({self.default_timeout}s)"}
        except Exception as exc:
            logger.opt(exception=True).error(f"Tool '{tool_name}' failed: {exc}")
            return {"error": str(exc)}

        # -- cache write --
        if cache_key:
            self._result_cache[cache_key] = (_time.time(), result)

        # -- audit --
        if tool.risk_level == RiskLevel.DESTRUCTIVE:
            self._write_audit(tool_name, args, ctx, result)

        return result if isinstance(result, dict) else {"result": result}

    # ------------------------------------------------------------------
    # cache management
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Clear all cached tool results."""
        self._result_cache.clear()

    # ------------------------------------------------------------------
    # audit
    # ------------------------------------------------------------------

    def _write_audit(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolCallContext,
        result: Any,
    ) -> None:
        """Append a destructive-tool record to the audit log."""
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": _time.time(),
                "tool": tool_name,
                "args": args,
                "user_id": ctx.user_id,
                "group_id": ctx.group_id,
                "result_summary": str(result)[:200],
            }
            with open(self.audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.opt(exception=True).warning("Failed to write audit log entry")


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


def _validate_args(tool: ToolDef, args: dict[str, Any]) -> str | None:
    """Check that required parameters are present. Returns error string or None."""
    required: list[str] = tool.parameters.get("required", [])
    if not required:
        return None
    for param in required:
        if param not in args:
            return f"缺少必要参数: {param}"
    return None


def _make_cache_key(tool_name: str, args: dict[str, Any]) -> str:
    """Build a deterministic cache key from tool name + args."""
    raw = json.dumps({"name": tool_name, "args": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()
