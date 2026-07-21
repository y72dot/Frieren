"""E2E test infrastructure: fixtures, state reset, and helpers.

Provides reusable fixtures for testing the full pipeline:
raw napcat event → EventBus → FilterManager → MessageBus →
Plugin.match/handle → API calls → _QQExec → _raw_call.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.config import LLMConfig
from src.core.message_bus import MessageType

# ---------------------------------------------------------------------------
# Re-export conftest.py fixtures so E2E test files can import from here
# ---------------------------------------------------------------------------

# These are discovered automatically by pytest; the imports below are just
# to satisfy type-checkers and avoid "unused import" warnings.
from tests.conftest import (  # noqa: F401
    FakeLlmProvider,
    _FakeApiClient,
    bot,
    bot_config,
    bot_with_llm,
    bus,
    event_group,
    event_private,
    fake_llm,
    mock_api_client,
    plugin_manager,
)


# ---------------------------------------------------------------------------
# State reset
# ---------------------------------------------------------------------------


def _reset_all_module_state() -> None:
    """Clear all module-level mutable state between E2E tests."""
    import plugins.llm_core as lc
    from plugins.action_queue import reset_state as reset_aq

    lc._session_cache.clear()
    # Reassign (don't .clear()) because _tools_registry may share the
    # same list object as llm_tools.TOOL_DEFS installed by _lazy_init.
    lc._tools_registry = []
    reset_aq()

    # Character doc cache
    import plugins.llm_tools as lt

    lt._CHARACTER_SECTIONS = None
    lt._CHARACTER_FULL_TEXT = None

    # Repeater plugin state (may not be loaded)
    try:
        import plugins.repeater as rp

        rp._last_repeated.clear()
        rp._locks.clear()
    except (ImportError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# LLM handler registration
# ---------------------------------------------------------------------------


def _register_llm_handlers(bot) -> None:
    """Register llm_core / llm_tools / llm_sender as INTERNAL handlers."""
    from plugins.llm_core import _lazy_init, llm_core_handler
    from plugins.llm_sender import llm_sender_handler
    from plugins.llm_tools import llm_tools_handler
    from src.plugin.manager import _SubscribeAdapter

    _lazy_init(bot)
    bot.message_bus.subscribe(
        MessageType.INTERNAL,
        _SubscribeAdapter(llm_core_handler, "llm_core", 50),
        50,
    )
    bot.message_bus.subscribe(
        MessageType.INTERNAL,
        _SubscribeAdapter(llm_tools_handler, "llm_tools", 30),
        30,
    )
    bot.message_bus.subscribe(
        MessageType.INTERNAL,
        _SubscribeAdapter(llm_sender_handler, "llm_sender", 40),
        40,
    )


# ---------------------------------------------------------------------------
# E2E fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_bot(bot_config):
    """Full Bot instance with fake API, LLM provider, and in-memory store.

    LLM core/tools/sender handlers are registered on the INTERNAL bus.
    """
    from src.core.bot import Bot
    from src.core.message_store import MessageStore

    _reset_all_module_state()
    bot_config.llm = LLMConfig(
        enabled=True,
        api_base="https://fake-api.example.com/v1",
        api_key="sk-fake",
        model="fake-model",
        max_tokens=512,
        temperature=0.0,
        max_turns=3,
    )
    b = Bot(config=bot_config)
    b.api = _FakeApiClient()
    b.llm_provider = FakeLlmProvider()
    b.msg_store = MessageStore(db_path=":memory:")
    _register_llm_handlers(b)
    return b


@pytest.fixture
def e2e_llm_bot(e2e_bot):
    """Like e2e_bot but with LlmGatePlugin registered as EXTERNAL entry point."""
    from plugins.llm_gate import LlmGatePlugin

    e2e_bot.plugin_manager.register(LlmGatePlugin())
    return e2e_bot


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


async def dispatch_raw_event(bot, raw_dict: dict) -> None:
    """Inject a raw napcat dict event and flush all subsequent messages."""
    await bot.event_bus.dispatch(raw_dict, bot)
    await bot.message_bus.flush(bot)


def assert_api_called(bot, method: str, **params: Any) -> None:
    """Assert that bot.api was called with the given method + optional params."""
    calls = [
        c
        for c in bot.api.calls
        if c.get("method") == method or c.get("action") == method
    ]
    assert calls, f"Expected API call '{method}' not found in {bot.api.calls}"
    if params:
        assert any(
            all(c.get(k) == v for k, v in params.items()) for c in calls
        ), f"No call matching {params} found in {calls}"
