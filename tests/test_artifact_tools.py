from __future__ import annotations

from plugins.llm_artifact_tools import register_artifact_tools
from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog


def test_registers_artifact_agent_tools_with_risk_levels():
    catalog = ToolCatalog()
    register_artifact_tools(catalog)

    assert catalog.count == 4
    assert catalog.get("list_message_artifacts").risk_level is RiskLevel.READ_ONLY
    assert catalog.get("materialize_artifact").risk_level is RiskLevel.WRITE
    assert catalog.get("send_artifact").risk_level is RiskLevel.WRITE
