from src.core.bot import Bot
from src.plugin.base import Event
from src.plugin.decorators import command


@command("/ping", priority=0)
async def ping(event: Event, bot: Bot) -> bool:
    if event.is_group:
        await bot.api.send_group_msg(event.group_id, "Pong!")
    else:
        await bot.api.send_private_msg(event.user_id, "Pong!")
    return True
