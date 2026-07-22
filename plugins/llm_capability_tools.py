"""Compatibility alias for the capability tool provider."""

import sys

from src.core.llm.tools.providers import capability as _provider

sys.modules[__name__] = _provider
