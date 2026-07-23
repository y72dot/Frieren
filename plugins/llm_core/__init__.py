"""LLM Core package – thin MessageBus adapter for the LLM agent service.

The ``sys.modules`` trick is preserved for backward compatibility with
code that does ``from plugins.llm_core import ...`` expecting the service.
"""

import sys

from plugins.llm_core.plugin import LlmCorePlugin  # noqa: F401
from src.core.llm import agent_service as _service

# -- Backwards compat: expose service internals at package level --
_lazy_init = _service._lazy_init
_session_cache = _service._session_cache
_tools_registry = _service._tools_registry

# -- Legacy-format handler for E2E test backward compat --
_instance = LlmCorePlugin()


async def llm_core_handler(payload, bot) -> bool:
    """Legacy-compatible (payload, bot) → bool wrapper."""
    result = await _instance.handle_trigger(payload, bot)
    return result.to_bool() if hasattr(result, "to_bool") else bool(result)


_service.llm_core_handler = llm_core_handler
sys.modules[__name__] = _service
