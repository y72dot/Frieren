"""Role-based permission checking for tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolDef


@dataclass
class ToolCallContext:
    """Context available to the tool executor for permission decisions."""

    user_id: int
    group_id: int | None
    user_is_admin: bool          # is the triggering user a bot admin_user?
    bot_is_group_owner: bool = False
    bot_is_group_admin: bool = False
    task_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    invocation_id: str | None = None
    trace_id: str = ""
    config_snapshot_id: str = ""
    granted_capabilities: set[str] = field(default_factory=set)
    idempotency_key: str | None = None


def check_permission(tool: ToolDef, ctx: ToolCallContext) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for a tool call in the given context.

    Rules:
    - READ_ONLY tools: always allowed.
    - WRITE tools: always allowed (rate-limiting is handled by the action queue).
    - DESTRUCTIVE tools: allowed only for bot admin users.
    - ``requires_admin`` flag: additional gate for admin-only tools.
    """
    # Explicit approval is a per-invocation safety boundary, including admins.
    if (
        tool.approval == "required"
        and f"approval:{tool.name}" not in ctx.granted_capabilities
    ):
        return False, f"tool {tool.name} requires approval"

    conversation_context = "group" if ctx.group_id is not None else "private"
    if conversation_context not in tool.contexts:
        return False, f"tool {tool.name} is unavailable in {conversation_context} context"

    audience = "admin" if ctx.user_is_admin else "user"
    if audience not in tool.audiences:
        return False, f"tool {tool.name} is unavailable for {audience} audience"

    # Admin users bypass role and capability gates after explicit approval.
    if ctx.user_is_admin:
        return True, "admin"

    # Non-admin destructive tools are blocked
    if tool.risk_level == RiskLevel.DESTRUCTIVE:
        return False, f"工具 {tool.name} 需要管理员权限 (risk={tool.risk_level.value})"

    # requires_admin flag blocks non-admin
    if tool.requires_admin:
        return False, f"工具 {tool.name} 仅限管理员使用"

    if tool.scopes and not tool.scopes.issubset(ctx.granted_capabilities):
        missing = sorted(tool.scopes - ctx.granted_capabilities)
        return False, f"tool {tool.name} missing capabilities: {', '.join(missing)}"

    return True, "ok"
