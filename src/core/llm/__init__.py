"""LLM subsystem – provider abstraction, session management, and tools."""

# ruff: noqa: I001 - import order is dependency order for this facade module.
from src.core.llm.provider import (
    LlmProvider,
    LlmResponse,
    OpenAICompatibleProvider,
    ToolCall,
)
from src.core.llm.session_logger import LlmSessionLogger

# Phase 1: tool infrastructure
from src.core.llm.sandbox import RiskLevel
from src.core.llm.tool_catalog import ToolCatalog, ToolDef
from src.core.llm.tool_permissions import ToolCallContext, check_permission
from src.core.llm.tool_selector import ToolSelectionRequest, ToolSelector
from src.core.llm.tool_view import ToolView
from src.core.llm.tool_metrics import ToolMetrics, ToolMetricsSnapshot
from src.core.llm.invocation_store import InvocationStore, ToolInvocation
from src.core.llm.tool_executor import ToolExecutor

# Phase 2: session management
from src.core.llm.session_manager import Session, SessionManager

# Phase 3: agent loop and orchestration
from src.core.llm.circuit_breaker import CircuitBreaker
from src.core.llm.agent_loop import AgentLoop, AgentResult, LoopConfig
from src.core.llm.agent_service import LlmAgentService

# Phase 4: memory
from src.core.llm.memory_manager import MemoryConfig, MemoryManager

# Phase 5: skills
from src.core.llm.skill_manager import SkillDef, SkillManager, SkillsConfig

# Phase 6: sandbox
from src.core.llm.sandbox_manager import SandboxConfig, SandboxManager

__all__ = [
    # provider
    "LlmProvider",
    "LlmResponse",
    "LlmSessionLogger",
    "OpenAICompatibleProvider",
    "ToolCall",
    # sandbox
    "RiskLevel",
    # tool_catalog
    "ToolCatalog",
    "ToolDef",
    # tool_permissions
    "ToolCallContext",
    "check_permission",
    "ToolSelectionRequest",
    "ToolSelector",
    "ToolView",
    "ToolMetrics",
    "ToolMetricsSnapshot",
    # tool_executor
    "ToolExecutor",
    "InvocationStore",
    "ToolInvocation",
    # session_manager
    "Session",
    "SessionManager",
    # circuit_breaker
    "CircuitBreaker",
    # agent_loop
    "AgentLoop",
    "AgentResult",
    "LoopConfig",
    "LlmAgentService",
    # memory
    "MemoryConfig",
    "MemoryManager",
    # skills
    "SkillDef",
    "SkillManager",
    "SkillsConfig",
    # sandbox
    "SandboxConfig",
    "SandboxManager",
]
