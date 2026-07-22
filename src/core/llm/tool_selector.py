"""Deterministic per-request selection for the global LLM tool catalog."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef
from src.core.llm.tool_permissions import ToolCallContext
from src.core.llm.tool_view import ToolView


@dataclass(frozen=True)
class ToolSelectionRequest:
    """Inputs that may affect which registered tools are shown to the model."""

    user_text: str
    conversation_type: str
    enabled_packs: frozenset[str] = field(default_factory=frozenset)
    disabled_packs: frozenset[str] = field(default_factory=frozenset)


class ToolSelector:
    """Build a stable ToolView using context, authorization, and intent packs."""

    PACK_PATTERNS: dict[str, tuple[str, ...]] = {
        "group_read": ("群成员", "成员列表", "群信息", "群资料", "精华列表", "禁言列表"),
        "moderation": (
            "禁言",
            "踢出",
            "踢人",
            "撤回",
            "全员禁言",
            "管理员",
            "群名片",
            "设精",
            "精华",
        ),
        "interaction": ("点赞", "戳一戳", "戳他", "表情回应", "单独通知"),
        "perception": ("图片", "语音", "视频", "文件", "转发", "ocr", "识别"),
        "knowledge": ("芙莉莲", "辛美尔", "菲伦", "修塔尔克", "赛丽艾", "魔法设定"),
        "search": ("搜索消息", "查找消息", "历史记录", "搜索记忆", "搜索任务"),
        "workspace": ("工作区", "读取文件", "写入文件", "导出文件", "生成文件"),
        "web": ("联网", "网页", "网站", "网址", "新闻", "最新", "http://", "https://"),
        "schedule": ("提醒", "定时", "每隔", "每天", "每周", "cron", "计划任务"),
        "control": ("修改设置", "修改配置", "prompt", "安装插件", "禁用插件", "回滚插件"),
        "sandbox": ("运行代码", "执行命令", "python", "shell", "沙箱", "计算脚本"),
    }

    def select(
        self,
        catalog: ToolCatalog,
        ctx: ToolCallContext,
        request: ToolSelectionRequest,
    ) -> ToolView:
        context = request.conversation_type
        active_packs = {"core", f"{context}_core", *request.enabled_packs}
        normalized = request.user_text.casefold()

        for pack, patterns in self.PACK_PATTERNS.items():
            if any(pattern.casefold() in normalized for pattern in patterns):
                active_packs.add(pack)

        # Explicit tool-name mentions activate that tool's packs without an
        # additional router model call.
        for tool in catalog:
            if tool.name.casefold() in normalized:
                active_packs.update(tool.packs)

        active_packs.difference_update(request.disabled_packs)

        selected: list[ToolDef] = []
        for tool in catalog:
            if not self._is_visible(tool, ctx, context):
                continue
            enabled = tool.default_enabled or bool(tool.packs & active_packs)
            if not enabled and self._matches_intents(tool, normalized):
                enabled = True
                active_packs.update(tool.packs)
            if enabled:
                selected.append(tool)

        return ToolView(tuple(selected), tuple(sorted(active_packs)))

    @staticmethod
    def _is_visible(
        tool: ToolDef,
        ctx: ToolCallContext,
        conversation_type: str,
    ) -> bool:
        if conversation_type not in tool.contexts:
            return False
        audience = "admin" if ctx.user_is_admin else "user"
        if audience not in tool.audiences:
            return False
        if tool.requires_admin and not ctx.user_is_admin:
            return False
        if tool.risk_level == RiskLevel.DESTRUCTIVE and not ctx.user_is_admin:
            return False
        if (
            tool.approval == "required"
            and f"approval:{tool.name}" not in ctx.granted_capabilities
        ):
            return False
        return (
            ctx.user_is_admin
            or not tool.scopes
            or tool.scopes.issubset(ctx.granted_capabilities)
        )

    @classmethod
    def _matches_intents(cls, tool: ToolDef, normalized_text: str) -> bool:
        for intent in tool.intents:
            normalized_intent = intent.casefold().strip()
            if len(normalized_intent) >= 2 and normalized_intent in normalized_text:
                return True
            for token in cls._intent_tokens(normalized_intent):
                if token in normalized_text:
                    return True
        return False

    @staticmethod
    def _intent_tokens(intent: str) -> set[str]:
        tokens = {
            token
            for token in re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", intent)
            if token not in {"查询", "信息", "使用", "工具"}
        }
        for chinese in re.findall(r"[\u4e00-\u9fff]{3,}", intent):
            tokens.update(
                chinese[index : index + 2]
                for index in range(len(chinese) - 1)
                if chinese[index : index + 2] not in {"查询", "信息", "使用", "工具"}
            )
        return tokens
