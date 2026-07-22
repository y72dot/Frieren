"""Compatibility alias for the schedule tool provider."""

import sys

from src.core.llm.tools.providers import schedule as _provider

sys.modules[__name__] = _provider
