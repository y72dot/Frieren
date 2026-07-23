"""Action Queue Plugin: rate-limited delayed execution with filtering.

Intercepts all ACTION messages via ``ActionQueueMiddleware`` inside a
``MiddlewarePipeline`` registered at priority 0 (before _QQExec at 100).
Applies configurable rate limiting: global actions-per-second, per-group
cooldown, and per-action delay.  Also supports bypass / block lists for
action-level filtering.

Configuration lives in ``[action_queue]`` of ``bot.toml``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from loguru import logger

if hasattr(__import__("typing"), "TYPE_CHECKING"):
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from src.plugin.middleware import CallNext


class ActionQueueMiddleware:
    """Rate limiting and spam filtering for QQ API actions.

    Used as a middleware in the ``MiddlewarePipeline`` when
    ``PluginRuntime`` is active.  Each instance owns its state so the
    class is safe to recreate on hot-reload.
    """

    name = "action_queue"
    priority = 1

    def __init__(self, config=None) -> None:
        self._configured = config is not None
        self._enabled = config.enabled if config else True
        self._bypass_actions = set(config.bypass_actions) if config else set()
        self._block_actions = set(config.block_actions) if config else set()
        self._global_rate = config.global_rate if config else 5.0
        self._group_cooldown = config.group_cooldown if config else 1.0
        self._per_action_delay = config.per_action_delay if config else 0.0
        self._spam_window = config.spam_window if config else 5.0
        self._spam_actions = set(config.spam_actions) if config else set()

        self._semaphore = asyncio.Semaphore(1)
        self._last_action_time: float = 0.0
        self._group_last_time: dict[int, float] = {}
        self._spam_last: dict[str, float] = {}
        self._spam_lock = asyncio.Lock()
        self._SPAM_MAX_ENTRIES = 5000

    # ------------------------------------------------------------------
    # middleware protocol
    # ------------------------------------------------------------------

    async def process(
        self, action: str, params: dict[str, Any], call_next: CallNext
    ) -> dict[str, Any]:
        """Process an ACTION through rate limiting and spam filtering."""
        if not self._enabled:
            return await call_next(action, params)

        # Block.
        if action in self._block_actions:
            logger.info(f"ActionQueue: blocked action '{action}'")
            return {"status": "blocked", "reason": "action_queue", "action": action}

        # Bypass.
        if action in self._bypass_actions:
            return await call_next(action, params)

        # Spam check.
        if await self._check_spam(action, params):
            return {"status": "blocked", "reason": "spam", "action": action}

        # Rate limit.
        await self._acquire_rate_limit(params)

        return await call_next(action, params)

    # ------------------------------------------------------------------
    # spam / dedup filter (instance-level)
    # ------------------------------------------------------------------

    def _make_spam_key(self, action: str, params: dict) -> str:
        """Build a stable dedup key from action name and sorted params."""
        params_json = json.dumps(params, sort_keys=True, ensure_ascii=False)
        return f"{action}|{params_json}"

    async def _check_spam(self, action: str, params: dict) -> bool:
        """Return True if this action is a duplicate within the spam window."""
        if self._spam_window <= 0:
            return False

        if action not in self._spam_actions:
            return False

        key = self._make_spam_key(action, params)
        async with self._spam_lock:
            now = time.monotonic()
            last = self._spam_last.get(key)
            if last is not None and (now - last) < self._spam_window:
                logger.info(
                    f"ActionQueue: spam blocked '{action}' "
                    f"(duplicate within {self._spam_window:.1f}s)"
                )
                return True
            self._spam_last[key] = now
            self._maybe_cleanup_spam(now)
            return False

    def _maybe_cleanup_spam(self, now: float) -> None:
        """Lazy cleanup of expired entries when dict exceeds threshold."""
        if len(self._spam_last) <= self._SPAM_MAX_ENTRIES:
            return

        cutoff = now - self._spam_window * 2
        stale = [k for k, t in self._spam_last.items() if t < cutoff]
        for k in stale:
            del self._spam_last[k]
        if stale:
            logger.debug(
                f"ActionQueue: spam cleanup removed {len(stale)} stale entries "
                f"(remaining: {len(self._spam_last)})"
            )

    # ------------------------------------------------------------------
    # rate limiter (instance-level)
    # ------------------------------------------------------------------

    async def _acquire_rate_limit(self, params: dict) -> None:
        """Wait until the rate limit permits this action to proceed."""
        async with self._semaphore:
            now = time.monotonic()

            # -- global rate limit --
            if self._global_rate > 0:
                interval = 1.0 / self._global_rate
                wait = self._last_action_time + interval - now
                if wait > 0:
                    logger.debug(f"ActionQueue: global rate limit, waiting {wait:.2f}s")
                    await asyncio.sleep(wait)

            # -- per-group cooldown --
            group_id = params.get("group_id")
            if isinstance(group_id, int) and self._group_cooldown > 0:
                last = self._group_last_time.get(group_id, 0)
                group_wait = last + self._group_cooldown - time.monotonic()
                if group_wait > 0:
                    logger.debug(
                        f"ActionQueue: group {group_id} cooldown, waiting {group_wait:.2f}s"
                    )
                    await asyncio.sleep(group_wait)

            # -- per-action extra delay --
            if self._per_action_delay > 0:
                await asyncio.sleep(self._per_action_delay)

            # Update timestamps after all delays.
            self._last_action_time = time.monotonic()
            if isinstance(group_id, int):
                self._group_last_time[group_id] = time.monotonic()


# ---------------------------------------------------------------------------
# Bus-compatible adapter – bridges middleware to match/handle protocol
# (kept for backwards compat with existing test suites)
# ---------------------------------------------------------------------------


class ActionQueueBusAdapter:
    """Wraps ``ActionQueueMiddleware`` as a bus handler protocol object.

    Registered at priority 1 on ACTION type for tests that don't use
    the full ``MiddlewarePipeline`` path.
    """

    name = "action_queue"
    priority = 1

    def __init__(self, config=None):
        self._mw = ActionQueueMiddleware(config=config)

    def match(self, payload) -> bool:
        return isinstance(payload, dict) and "action" in payload

    async def handle(self, payload: dict, bot) -> dict | bool:
        action = payload.get("action", "")
        params = {k: v for k, v in payload.items() if k not in ("action",)}

        async def _call_next(a, p):
            if bot is not None and hasattr(bot, "api"):
                return await bot.api._raw_call(a, **p)
            return {"status": "ok"}

        result = await self._mw.process(action, params, _call_next)
        if isinstance(result, dict) and result.get("status") == "blocked":
            return True  # consume the message
        return result  # dict result or False to pass through


def reset_state() -> None:
    """Reset middleware state (test helper).

    .. deprecated:: P6
       Use a fresh ``ActionQueueMiddleware`` instance instead.
       Kept for backwards compat with existing tests.
    """
    pass  # no-op: middleware is instance-based now, not module-level
