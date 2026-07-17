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
    target = _get(event.raw, "target_id", 0)
    group_id = event.group_id
    await bot.api.send_group_poke(group_id, int(target))
    return True
