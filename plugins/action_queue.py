"""Action Queue Plugin: rate-limited delayed execution with filtering.

Intercepts all ACTION messages at priority 1 (before _QQExec at 100) and
applies configurable rate limiting: global actions-per-second, per-group
cooldown, and per-action delay.  Also supports bypass / block lists for
action-level filtering.

Configuration lives in ``[plugin.action_queue]`` of ``bot.toml``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

from loguru import logger

from src.core.message_bus import MessageType
from src.plugin.decorators import subscribe

if TYPE_CHECKING:
    from src.core.bot import Bot

# ---------------------------------------------------------------------------
# module-level state (populated on first ACTION dispatch)
# ---------------------------------------------------------------------------

_configured = False
_enabled: bool = True
_bypass_actions: set[str] = set()
_block_actions: set[str] = set()
_global_rate: float = 5.0
_group_cooldown: float = 1.0
_per_action_delay: float = 0.0

_action_semaphore = asyncio.Semaphore(1)
_last_action_time: float = 0.0
_group_last_time: dict[int, float] = {}

# -- spam / dedup state --
_spam_window: float = 5.0
_spam_actions: set[str] = set()
_spam_last: dict[str, float] = {}
_spam_lock = asyncio.Lock()
_SPAM_MAX_ENTRIES = 5000


# ---------------------------------------------------------------------------
# public API (for tests)
# ---------------------------------------------------------------------------


def reset_state() -> None:
    """Reset all module-level state (test helper)."""
    global _configured, _enabled, _bypass_actions, _block_actions
    global _global_rate, _group_cooldown, _per_action_delay
    global _action_semaphore, _last_action_time, _group_last_time
    global _spam_window, _spam_actions, _spam_last, _spam_lock

    _configured = False
    _enabled = True
    _bypass_actions = set()
    _block_actions = set()
    _global_rate = 5.0
    _group_cooldown = 1.0
    _per_action_delay = 0.0
    _action_semaphore = asyncio.Semaphore(1)
    _last_action_time = 0.0
    _group_last_time = {}
    _spam_window = 5.0
    _spam_actions = set()
    _spam_last = {}
    _spam_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# config loading
# ---------------------------------------------------------------------------


def _configure(bot: Bot) -> None:
    """Read plugin config from bot.config on first use."""
    global _configured, _enabled, _bypass_actions, _block_actions
    global _global_rate, _group_cooldown, _per_action_delay
    global _spam_window, _spam_actions

    cfg = bot.config.action_queue
    _enabled = cfg.enabled
    _bypass_actions = set(cfg.bypass_actions)
    _block_actions = set(cfg.block_actions)
    _global_rate = cfg.global_rate
    _group_cooldown = cfg.group_cooldown
    _per_action_delay = cfg.per_action_delay
    _spam_window = cfg.spam_window
    _spam_actions = set(cfg.spam_actions)
    _configured = True

    logger.debug(
        f"ActionQueue configured: enabled={_enabled} "
        f"global_rate={_global_rate}/s group_cooldown={_group_cooldown}s "
        f"per_action_delay={_per_action_delay}s "
        f"spam_window={_spam_window}s spam_actions={len(_spam_actions)} "
        f"bypass={len(_bypass_actions)} block={len(_block_actions)}"
    )


# ---------------------------------------------------------------------------
# spam / dedup filter
# ---------------------------------------------------------------------------


def _make_spam_key(action: str, payload: dict) -> str:
    """Build a stable dedup key from action name and sorted params."""
    params = {k: v for k, v in payload.items() if k != "action"}
    params_json = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return f"{action}|{params_json}"


async def _check_spam(payload: dict) -> bool:
    """Return True if this action is a duplicate within the spam window.

    Must be called after bypass check (bypassed actions skip spam).
    Uses a dedicated ``_spam_lock`` so duplicates are rejected
    immediately without waiting for the rate-limit semaphore.
    """
    if _spam_window <= 0:
        return False

    action: str = payload.get("action", "")
    if action not in _spam_actions:
        return False

    key = _make_spam_key(action, payload)
    async with _spam_lock:
        now = time.monotonic()
        last = _spam_last.get(key)
        if last is not None and (now - last) < _spam_window:
            logger.info(
                f"ActionQueue: spam blocked '{action}' "
                f"(duplicate within {_spam_window:.1f}s)"
            )
            return True
        _spam_last[key] = now
        _maybe_cleanup_spam(now)
        return False


def _maybe_cleanup_spam(now: float) -> None:
    """Lazy cleanup of expired entries when dict exceeds threshold.

    Caller must hold ``_spam_lock``.
    """
    if len(_spam_last) <= _SPAM_MAX_ENTRIES:
        return

    cutoff = now - _spam_window * 2
    stale = [k for k, t in _spam_last.items() if t < cutoff]
    for k in stale:
        del _spam_last[k]
    if stale:
        logger.debug(
            f"ActionQueue: spam cleanup removed {len(stale)} stale entries "
            f"(remaining: {len(_spam_last)})"
        )


# ---------------------------------------------------------------------------
# rate limiter
# ---------------------------------------------------------------------------


async def _acquire_rate_limit(payload: dict) -> None:
    """Wait until the rate limit permits this action to proceed.

    Serialises all actions through a semaphore so only one action
    executes at a time.  Other callers queue up on the semaphore,
    achieving the desired queue-execution semantics.
    """
    async with _action_semaphore:
        global _last_action_time

        now = time.monotonic()

        # -- global rate limit --
        if _global_rate > 0:
            interval = 1.0 / _global_rate
            wait = _last_action_time + interval - now
            if wait > 0:
                logger.debug(f"ActionQueue: global rate limit, waiting {wait:.2f}s")
                await asyncio.sleep(wait)

        # -- per-group cooldown --
        group_id = payload.get("group_id")
        if isinstance(group_id, int) and _group_cooldown > 0:
            last = _group_last_time.get(group_id, 0)
            group_wait = last + _group_cooldown - time.monotonic()
            if group_wait > 0:
                logger.debug(
                    f"ActionQueue: group {group_id} cooldown, waiting {group_wait:.2f}s"
                )
                await asyncio.sleep(group_wait)

        # -- per-action extra delay --
        if _per_action_delay > 0:
            await asyncio.sleep(_per_action_delay)

        # Update timestamps after all delays.
        _last_action_time = time.monotonic()
        if isinstance(group_id, int):
            _group_last_time[group_id] = time.monotonic()


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------


@subscribe(MessageType.ACTION, priority=1)
async def action_queue_handler(payload: object, bot: Bot) -> bool:
    """Intercept ACTION messages for rate limiting and filtering.

    The handler receives the raw payload dict (not the BusMessage
    envelope) — the bus adapter unwraps ``msg.payload`` before
    calling the subscribe handler.

    Returns
    -------
    bool
        * ``False`` – pass through to _QQExec (bypass / rate-limited).
        * ``True``  – consume / drop (blocked action).
    """
    global _configured

    if not _configured:
        _configure(bot)

    if not _enabled:
        return False

    if not isinstance(payload, dict):
        return False

    action: str = payload.get("action", "")
    if not action:
        return False

    # -- block --
    if action in _block_actions:
        logger.info(f"ActionQueue: blocked action '{action}'")
        return True

    # -- bypass --
    if action in _bypass_actions:
        return False

    # -- spam / dedup --
    if await _check_spam(payload):
        return True

    # -- rate limit --
    await _acquire_rate_limit(payload)
    return False
