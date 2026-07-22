"""Compatibility alias for the QQ LLM tool provider."""

import sys

from src.core.llm.tools.providers import qq as _provider

sys.modules[__name__] = _provider
