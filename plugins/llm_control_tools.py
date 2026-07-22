"""Compatibility alias for the control-plane tool provider."""

import sys

from src.core.llm.tools.providers import control as _provider

sys.modules[__name__] = _provider
