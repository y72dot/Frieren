"""ToolView selection, visibility, and metadata contracts."""

from __future__ import annotations

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef
from src.core.llm.tool_permissions import ToolCallContext, check_permission
from src.core.llm.tool_selector import ToolSelectionRequest, ToolSelector
from src.core.llm.tools import register_builtin_tools, register_sandbox_tools


def _context(*, admin: bool = False, group: bool = True) -> ToolCallContext:
    return ToolCallContext(
        user_id=111,
        group_id=456 if group else None,
        user_is_admin=admin,
    )


def _catalog(*, sandbox: bool = False) -> ToolCatalog:
    catalog = ToolCatalog()
    register_builtin_tools(catalog)
    if sandbox:
        register_sandbox_tools(catalog)
    return catalog


def _select(
    text: str,
    *,
    admin: bool = False,
    group: bool = True,
    sandbox: bool = False,
    enabled: frozenset[str] = frozenset(),
    disabled: frozenset[str] = frozenset(),
):
    context = _context(admin=admin, group=group)
    request = ToolSelectionRequest(
        user_text=text,
        conversation_type="group" if group else "private",
        enabled_packs=enabled,
        disabled_packs=disabled,
    )
    return ToolSelector().select(_catalog(sandbox=sandbox), context, request)


def test_default_views_are_small_stable_and_contextual():
    first = _select("你好")
    second = _select("你好")
    private = _select("你好", group=False)

    assert first.names == second.names
    assert 6 <= len(first) <= 12
    assert set(first.names) == {
        "react_emoji",
        "get_current_time",
        "query_history",
        "tool_help",
        "resolve_forward",
        "get_group_info",
        "get_member_info",
        "list_message_artifacts",
    }
    assert "get_group_info" not in private.names
    assert len(private) == 6


def test_admin_intent_activates_moderation_without_exposing_it_to_users():
    admin = _select("请禁言这个成员并撤回消息", admin=True)
    user = _select("请禁言这个成员并撤回消息")

    assert {"mute_user", "delete_msg", "kick_user"}.issubset(admin.names)
    assert "moderation" in admin.active_packs
    assert "mute_user" not in user.names
    assert "delete_msg" not in user.names


def test_feature_packs_are_intent_gated_and_admin_filtered():
    normal = _select("你好", admin=True, sandbox=True)
    web = _select("请联网搜索最新新闻", admin=True)
    sandbox = _select("用 Python 运行代码计算结果", admin=True, sandbox=True)
    non_admin_web = _select("请联网搜索最新新闻")

    assert "web_search" not in normal.names
    assert {"web_search", "web_fetch", "web_download"}.issubset(web.names)
    assert {"sandbox_exec", "sandbox_read", "sandbox_write"}.issubset(sandbox.names)
    assert "web_search" not in non_admin_web.names


def test_context_and_explicit_pack_controls_are_hard_filters():
    group = _select("点赞", enabled=frozenset({"interaction"}))
    private = _select("点赞", group=False, enabled=frozenset({"interaction"}))
    disabled = _select("请联网查看网页", admin=True, disabled=frozenset({"web"}))

    assert "send_poke" in group.names
    assert "send_like" not in group.names
    assert "send_like" in private.names
    assert "send_poke" not in private.names
    assert "web_search" not in disabled.names


def test_progressive_skill_intent_can_activate_one_custom_tool():
    async def execute(args, group_id, user_id, bot):
        return {}

    catalog = ToolCatalog()
    catalog.register(
        ToolDef(
            name="weather",
            description="查询天气信息",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.READ_ONLY,
            category="query",
            executor=execute,
            provider="skill",
            packs={"skill"},
            intents={"weather", "查询天气信息"},
            default_enabled=False,
        )
    )

    hidden = ToolSelector().select(
        catalog,
        _context(),
        ToolSelectionRequest("你好", "group"),
    )
    visible = ToolSelector().select(
        catalog,
        _context(),
        ToolSelectionRequest("厦门天气怎么样", "group"),
    )

    assert hidden.names == ()
    assert visible.names == ("weather",)


def test_execution_permission_reuses_context_and_audience_boundaries():
    catalog = _catalog()
    mute = catalog.get("mute_user")
    send_like = catalog.get("send_like")
    assert mute is not None and send_like is not None

    allowed, reason = check_permission(mute, _context(admin=False))
    assert allowed is False
    assert "audience" in reason

    allowed, reason = check_permission(send_like, _context(admin=True, group=True))
    assert allowed is False
    assert "context" in reason
