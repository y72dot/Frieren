"""Ping package plugin – responds to /ping with Pong!"""

from loguru import logger

from src.plugin import EventResult, command


class PingPlugin:
    __plugin_id__ = "ping"
    name = "ping"
    priority = 0

    @command("/ping")
    async def ping_cmd(self, event, ctx) -> EventResult:
        logger.info(f"ping: user={event.user_id} is_group={event.is_group}")
        await ctx.reply(event, "Pong!")
        return EventResult.CONSUME
