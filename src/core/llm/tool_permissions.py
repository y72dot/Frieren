"""Role-based permission checking for tool execution."""

from __future__ import annotations

from dataclasses import dataclass

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


def check_permission(tool: ToolDef, ctx: ToolCallContext) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for a tool call in the given context.

    Rules:
    - READ_ONLY tools: always allowed.
    - WRITE tools: always allowed (rate-limiting is handled by the action queue).
    - DESTRUCTIVE tools: allowed only for bot admin users.
    - ``requires_admin`` flag: additional gate for admin-only tools.
    """
    # Admin users can do anything
    if ctx.user_is_admin:
        return True, "admin"

    # Non-admin destructive tools are blocked
    if tool.risk_level == RiskLevel.DESTRUCTIVE:
        return False, f"工具 {tool.name} 需要管理员权限 (risk={tool.risk_level.value})"

    # requires_admin flag blocks non-admin
    if tool.requires_admin:
        return False, f"工具 {tool.name} 仅限管理员使用"

    return True, "ok"
