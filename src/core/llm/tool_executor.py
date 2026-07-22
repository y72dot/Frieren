"""Validated, permissioned, persistent tool execution pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time as _time
from typing import Any

from loguru import logger

from src.core.llm.invocation_store import InvocationStore
from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef
from src.core.llm.tool_permissions import ToolCallContext, check_permission

_audit_log = logger.bind(__log_channel="_audit")


class ToolExecutor:
    """Execute tool calls through validation, policy, persistence and audit."""

    def __init__(
        self,
        catalog: ToolCatalog,
        default_timeout: float = 30.0,
        invocation_store: InvocationStore | None = None,
        max_result_bytes: int = 262_144,
    ) -> None:
        self.catalog = catalog
        self.default_timeout = default_timeout
        self.invocation_store = invocation_store
        self.max_result_bytes = max_result_bytes
        self._result_cache: dict[str, tuple[float, Any]] = {}

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolCallContext,
        bot: Any,
    ) -> dict[str, Any]:
        tool = self.catalog.get(tool_name)
        if tool is None:
            error = f"unknown tool: {tool_name}"
            invocation = self._begin_unknown(tool_name, args, ctx)
            self._finish(invocation, "invalid", error=error)
            return {"error": error}

        idempotency_key = _idempotency_key(tool, args, ctx)
        if self.invocation_store is not None and idempotency_key:
            previous = self.invocation_store.find_succeeded(tool_name, idempotency_key)
            if previous is not None:
                result = previous.result()
                return result if isinstance(result, dict) else {"result": result}

        invocation = None
        if self.invocation_store is not None:
            invocation = self.invocation_store.begin(
                tool_name=tool.name,
                tool_version=tool.version,
                arguments=args,
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                invocation_id=ctx.invocation_id,
                idempotency_key=idempotency_key,
                trace_id=ctx.trace_id,
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                config_snapshot_id=ctx.config_snapshot_id,
            )

        error = _validate_args(tool, args)
        if error:
            self._finish(invocation, "invalid", error=error)
            return {"error": error}

        allowed, reason = check_permission(tool, ctx)
        if not allowed:
            logger.warning(f"Tool '{tool_name}' denied: {reason}")
            self._finish(invocation, "denied", error=reason)
            return {"error": reason}

        cache_key = ""
        if tool.cache_ttl > 0 and tool.risk_level == RiskLevel.READ_ONLY:
            cache_key = _make_cache_key(tool_name, args)
            cached = self._result_cache.get(cache_key)
            if cached is not None:
                timestamp, value = cached
                if _time.time() - timestamp < tool.cache_ttl:
                    self._finish(invocation, "succeeded", result=value)
                    return value

        if invocation is not None:
            self.invocation_store.transition(invocation.invocation_id, "running")
        timeout = tool.timeout_seconds or self.default_timeout
        try:
            result = await asyncio.wait_for(
                tool.executor(args, ctx.group_id, ctx.user_id, bot),
                timeout=timeout,
            )
        except TimeoutError:
            error = f"工具执行超时 ({timeout}s)"
            logger.error(f"Tool '{tool_name}' timed out after {timeout}s")
            self._finish(invocation, "timed_out", error=error)
            return {"error": error}
        except Exception as exc:
            logger.opt(exception=True).error(f"Tool '{tool_name}' failed: {exc}")
            self._finish(invocation, "failed", error=str(exc))
            return {"error": str(exc)}

        normalized = result if isinstance(result, dict) else {"result": result}
        output_error = _validate_schema(normalized, tool.output_schema, path="result")
        if output_error:
            self._finish(invocation, "failed", error=output_error)
            return {"error": output_error}
        result_size = len(
            json.dumps(normalized, ensure_ascii=False, default=str).encode("utf-8")
        )
        if result_size > self.max_result_bytes:
            error = f"tool result exceeds {self.max_result_bytes} bytes"
            self._finish(invocation, "failed", error=error)
            return {"error": error}

        if cache_key:
            self._result_cache[cache_key] = (_time.time(), normalized)
        if tool.risk_level == RiskLevel.DESTRUCTIVE:
            self._write_audit(tool_name, args, ctx, normalized)
        self._finish(invocation, "succeeded", result=normalized)
        return normalized

    def _begin_unknown(
        self, tool_name: str, args: dict[str, Any], ctx: ToolCallContext
    ) -> Any:
        if self.invocation_store is None:
            return None
        return self.invocation_store.begin(
            tool_name=tool_name,
            tool_version="unknown",
            arguments=args,
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            invocation_id=ctx.invocation_id,
            idempotency_key=None,
            trace_id=ctx.trace_id,
            user_id=ctx.user_id,
            group_id=ctx.group_id,
            config_snapshot_id=ctx.config_snapshot_id,
        )

    def _finish(
        self,
        invocation: Any,
        status: str,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        if invocation is not None and self.invocation_store is not None:
            self.invocation_store.transition(
                invocation.invocation_id,
                status,
                result=result,
                error=error,
                terminal=True,
            )

    def clear_cache(self) -> None:
        self._result_cache.clear()

    def _write_audit(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolCallContext,
        result: Any,
    ) -> None:
        try:
            entry = {
                "timestamp": _time.time(),
                "tool": tool_name,
                "args": args,
                "user_id": ctx.user_id,
                "group_id": ctx.group_id,
                "invocation_id": ctx.invocation_id,
                "result_summary": str(result)[:200],
            }
            _audit_log.info(json.dumps(entry, ensure_ascii=False))
        except Exception:
            logger.opt(exception=True).warning("Failed to write audit log entry")


def _validate_args(tool: ToolDef, args: dict[str, Any]) -> str | None:
    return _validate_schema(args, tool.parameters, path="arguments")


def _make_cache_key(tool_name: str, args: dict[str, Any]) -> str:
    raw = json.dumps({"name": tool_name, "args": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _idempotency_key(
    tool: ToolDef, args: dict[str, Any], ctx: ToolCallContext
) -> str | None:
    if tool.idempotency != "keyed":
        return None
    if ctx.idempotency_key:
        return ctx.idempotency_key
    if not ctx.run_id:
        return None
    raw = json.dumps(
        {"run_id": ctx.run_id, "tool": tool.name, "args": args},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _validate_schema(value: Any, schema: dict[str, Any], *, path: str) -> str | None:
    if not schema:
        return None
    expected = schema.get("type")
    type_map: dict[str, Any] = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    if expected in type_map:
        numeric_bool = expected in {"integer", "number"} and isinstance(value, bool)
        if not isinstance(value, type_map[expected]) or numeric_bool:
            return f"{path} must be {expected}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of {schema['enum']}"
    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            return f"{path} must contain at least {schema['minProperties']} properties"
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            return f"{path} must contain at most {schema['maxProperties']} properties"
        for key in schema.get("required", []):
            if key not in value:
                return f"缺少必要参数: {key}"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                return f"{path} has unknown properties: {', '.join(extras)}"
        for key, item in value.items():
            if key in properties:
                error = _validate_schema(item, properties[key], path=f"{path}.{key}")
                if error:
                    return error
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return f"{path} must contain at least {schema['minItems']} items"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{path} must contain at most {schema['maxItems']} items"
        for index, item in enumerate(value):
            error = _validate_schema(
                item, schema.get("items", {}), path=f"{path}[{index}]"
            )
            if error:
                return error
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return f"{path} is shorter than minLength"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{path} is longer than maxLength"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path} must be >= {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path} must be <= {schema['maximum']}"
    return None
