"""Compatibility alias for the artifact tool provider."""

import sys

from src.core.llm.tools.providers import artifact as _provider

sys.modules[__name__] = _provider
