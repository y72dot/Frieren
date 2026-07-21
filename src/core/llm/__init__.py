"""LLM subsystem – provider abstraction, session management, and tools."""

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
from src.core.llm.tool_executor import ToolExecutor

# Phase 2: session management
from src.core.llm.session_manager import Session, SessionManager

# Phase 3: agent loop
from src.core.llm.circuit_breaker import CircuitBreaker
from src.core.llm.agent_loop import AgentLoop, AgentResult, LoopConfig

# Phase 4: memory
from src.core.llm.memory_manager import MemoryConfig, MemoryManager

# Phase 5: skills
from src.core.llm.skill_manager import SkillDef, SkillManager, SkillsConfig

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
    # tool_executor
    "ToolExecutor",
    # session_manager
    "Session",
    "SessionManager",
    # circuit_breaker
    "CircuitBreaker",
    # agent_loop
    "AgentLoop",
    "AgentResult",
    "LoopConfig",
    # memory
    "MemoryConfig",
    "MemoryManager",
    # skills
    "SkillDef",
    "SkillManager",
    "SkillsConfig",
]
