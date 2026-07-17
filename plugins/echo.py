from src.core.bot import Bot
from src.plugin.base import Event
from src.plugin.decorators import command


@command("/echo", priority=0)
async def echo(event: Event, bot: Bot) -> bool:
    content = event.message.removeprefix("/echo").strip()
    if not content:
        content = "Usage: /echo <message>"

    if event.is_group:
        await bot.api.send_group_msg(event.group_id, content)
    else:
        await bot.api.send_private_msg(event.user_id, content)
    return True
