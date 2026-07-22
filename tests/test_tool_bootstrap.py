"""Contracts for the LLM tool composition root and migration aliases."""

from src.core.llm.tool_catalog import ToolCatalog
from src.core.llm.tools import register_builtin_tools, register_sandbox_tools


def test_builtin_bootstrap_preserves_catalog_contract():
    catalog = ToolCatalog()

    register_builtin_tools(catalog)

    names = [tool.name for tool in catalog]
    assert names[:4] == ["set_essence", "react_emoji", "send_message", "mute_user"]
    assert {
        "query_history",
        "list_message_artifacts",
        "settings_get",
        "create_schedule",
    }.issubset(names)


def test_sandbox_provider_is_optional_and_deterministic():
    catalog = ToolCatalog()
    register_builtin_tools(catalog)

    register_sandbox_tools(catalog)

    assert [tool.name for tool in catalog][-2:] == [
        "sandbox_exec",
        "sandbox_delete",
    ]


def test_plugin_directory_has_no_tool_providers():
    from pathlib import Path

    assert not list(Path("plugins").glob("llm_*_tools.py"))
