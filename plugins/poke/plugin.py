"""Poke package plugin – pokes back when poked in a group."""

import time

from loguru import logger

from src.plugin import EventResult, on_event


def _get(raw: object, key: str, default: object = None) -> object:
    """Get a field from a dict or an object attribute."""
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


class PokePlugin:
    __plugin_id__ = "poke"
    name = "poke_back"
    priority = 0
    _DEDUP_WINDOW_SECONDS = 1.0

    def __init__(self) -> None:
        self._last_poke_signature: tuple[int, int, int, int] | None = None
        self._last_poke_seen_at = 0.0

    @on_event("notice.notify", priority=0)
    async def poke_back(self, event, ctx) -> EventResult:
        if _get(event.raw, "sub_type") != "poke" or not event.is_group:
            return EventResult.CONTINUE
        # Don't react to the bot's own pokes (infinite loop prevention)
        if event.user_id == ctx.config.bot_id:
            return EventResult.CONTINUE
        target = int(_get(event.raw, "target_id", 0))
        group_id = event.group_id
        # If the bot is the target, poke back the poker instead of self-poking
        if target == ctx.config.bot_id:
            target = event.user_id

        signature = (
            int(_get(event.raw, "time", 0)),
            int(group_id),
            int(event.user_id),
            target,
        )
        now = time.monotonic()
        if (
            signature == self._last_poke_signature
            and now - self._last_poke_seen_at < self._DEDUP_WINDOW_SECONDS
        ):
            logger.debug(
                f"poke: duplicate notice ignored poker={event.user_id} "
                f"target={target} grp={group_id}"
            )
            return EventResult.CONSUME

        self._last_poke_signature = signature
        self._last_poke_seen_at = now
        logger.info(f"poke: poker={event.user_id} target={target} grp={group_id}")
        try:
            await ctx.api.send_group_poke(group_id, target)
        except Exception:
            # Let a duplicate delivery retry if the original API call failed.
            self._last_poke_signature = None
            raise
        return EventResult.CONSUME
