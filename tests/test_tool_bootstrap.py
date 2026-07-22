"""Contracts for the LLM tool composition root and migration aliases."""

from src.core.llm.tool_catalog import ToolCatalog
from src.core.llm.tools import register_builtin_tools, register_sandbox_tools


def test_builtin_bootstrap_preserves_catalog_contract():
    catalog = ToolCatalog()

    register_builtin_tools(catalog)

    names = [tool.name for tool in catalog]
    assert len(names) == 54
    assert names[:4] == ["set_essence", "remove_essence", "react_emoji", "send_message"]
    assert {
        "query_history",
        "list_message_artifacts",
        "search_messages",
        "settings_get",
        "create_schedule",
    }.issubset(names)


def test_sandbox_provider_is_optional_and_deterministic():
    catalog = ToolCatalog()
    register_builtin_tools(catalog)

    register_sandbox_tools(catalog)

    assert catalog.count == 59
    assert [tool.name for tool in catalog][-5:] == [
        "sandbox_exec",
        "sandbox_write",
        "sandbox_read",
        "sandbox_list",
        "sandbox_delete",
    ]


def test_legacy_provider_imports_remain_compatible():
    import plugins.llm_artifact_tools as legacy_artifact
    import plugins.llm_tools as legacy_qq
    from src.core.llm.tools.providers import artifact, qq

    assert legacy_artifact is artifact
    assert legacy_qq is qq
    assert legacy_qq.register_llm_tools is qq.register_llm_tools
    assert not hasattr(legacy_qq, "llm_tools_handler")
