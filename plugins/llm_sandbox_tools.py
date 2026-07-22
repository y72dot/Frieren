"""Compatibility alias for the sandbox tool provider."""

import sys

from src.core.llm.tools.providers import sandbox as _provider

sys.modules[__name__] = _provider
