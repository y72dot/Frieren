"""Composition root for built-in LLM tool providers."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from src.core.llm.tool_catalog import ToolCatalog
from src.core.llm.tools.providers.artifact import register_artifact_tools
from src.core.llm.tools.providers.capability import register_capability_tools
from src.core.llm.tools.providers.control import register_control_tools
from src.core.llm.tools.providers.qq import register_llm_tools
from src.core.llm.tools.providers.sandbox import (
    register_sandbox_tools as _register_sandbox,
)
from src.core.llm.tools.providers.schedule import register_schedule_tools

ToolRegistrar = Callable[[ToolCatalog], None]

BUILTIN_PROVIDERS: tuple[ToolRegistrar, ...] = (
    register_llm_tools,
    register_artifact_tools,
    register_capability_tools,
    register_control_tools,
    register_schedule_tools,
)

_PACK_MEMBERS: dict[str, set[str]] = {
    "core": {
        "get_current_time",
        "query_history",
        "react_emoji",
        "resolve_forward",
        "list_message_artifacts",
    },
    "group_core": {"get_group_info", "get_member_info"},
    "group_read": {"get_member_list", "get_essence_list", "get_shut_list"},
    "moderation": {
        "set_essence",
        "mute_user",
        "kick_user",
        "set_group_card",
        "delete_msg",
        "whole_ban",
        "set_admin",
    },
    "interaction": {"react_emoji", "send_message", "send_poke", "send_like"},
    "perception": {
        "resolve_forward",
        "ocr_image",
        "voice_to_text",
        "list_message_artifacts",
        "get_artifact_info",
        "materialize_artifact",
        "send_artifact",
    },
    "knowledge": {"query_character"},
    "search": {
        "query_history",
        "search_artifacts",
        "search_workspace",
        "search_tasks",
        "search_memory",
    },
    "workspace": {
        "workspace_write",
        "workspace_read",
        "workspace_list",
        "workspace_export_artifact",
    },
    "web": {"web_search", "web_fetch", "web_download"},
    "control": {
        "settings_get",
        "settings_propose",
        "prompts_get",
        "prompts_propose",
        "plugins_list",
        "plugins_validate",
        "plugins_propose_install",
        "plugins_propose_state",
        "plugins_propose_rollback",
    },
    "schedule": {
        "create_schedule",
        "list_schedules",
        "set_schedule_enabled",
        "delete_schedule",
    },
    "sandbox": {
        "sandbox_exec",
        "sandbox_delete",
    },
}

_GROUP_ONLY = {
    "set_essence",
    "mute_user",
    "kick_user",
    "get_group_info",
    "get_member_info",
    "get_member_list",
    "get_essence_list",
    "get_shut_list",
    "set_group_card",
    "whole_ban",
    "set_admin",
}
_PRIVATE_ONLY = {"send_like"}
_ADMIN_AUDIENCE = _PACK_MEMBERS["moderation"] | _PACK_MEMBERS["control"] | _PACK_MEMBERS["sandbox"]


def register_providers(
    catalog: ToolCatalog,
    providers: Iterable[ToolRegistrar],
) -> None:
    """Register providers in deterministic order."""
    for register in providers:
        existing = {tool.name for tool in catalog}
        register(catalog)
        provider_name = register.__module__.rsplit(".", 1)[-1]
        for tool in catalog:
            if tool.name not in existing:
                tool.provider = provider_name
                _configure_builtin_metadata(tool)


def register_builtin_tools(catalog: ToolCatalog) -> None:
    """Register the always-available built-in tool providers."""
    register_providers(catalog, BUILTIN_PROVIDERS)


def register_sandbox_tools(catalog: ToolCatalog) -> None:
    """Register the optional Docker sandbox provider."""
    register_providers(catalog, (_register_sandbox,))


def _configure_builtin_metadata(tool) -> None:
    packs = {pack for pack, names in _PACK_MEMBERS.items() if tool.name in names}
    if not packs:
        raise RuntimeError(f"built-in tool has no pack metadata: {tool.name}")
    tool.packs = packs
    tool.default_enabled = False
    if tool.name in _GROUP_ONLY:
        tool.contexts = {"group"}
    elif tool.name in _PRIVATE_ONLY:
        tool.contexts = {"private"}
    else:
        tool.contexts = {"group", "private"}
    if tool.name in _ADMIN_AUDIENCE:
        tool.audiences = {"admin"}
    else:
        tool.audiences = {"user", "admin"}
