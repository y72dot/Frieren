from loguru import logger

from src.core.bot import Bot
from src.plugin.base import Event
from src.plugin.decorators import on_notice


def _get(raw: object, key: str, default: object = None) -> object:
    """Get a field from a dict or an object attribute."""
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


@on_notice("notify", priority=0)
async def poke_back(event: Event, bot: Bot) -> bool:
    if _get(event.raw, "sub_type") != "poke" or not event.is_group:
        return False
    # Don't react to the bot's own pokes (infinite loop prevention)
    if event.user_id == bot.config.bot.qq:
        return False
    target = int(_get(event.raw, "target_id", 0))
    group_id = event.group_id
    # If the bot is the target, poke back the poker instead of self-poking
    if target == bot.config.bot.qq:
        target = event.user_id
    logger.info(f"poke: poker={event.user_id} target={target} grp={group_id}")
    await bot.api.send_group_poke(group_id, target)
    return True
