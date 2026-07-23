"""Echo package plugin – echoes back the message following /echo."""

from loguru import logger

from src.plugin import EventResult, command


class EchoPlugin:
    __plugin_id__ = "echo"
    name = "echo"
    priority = 0

    @command("/echo")
    async def echo_cmd(self, event, ctx) -> EventResult:
        content = event.message.removeprefix("/echo").strip()
        logger.info(f"echo: user={event.user_id} content={content[:50]}")
        if not content:
            content = "Usage: /echo <message>"
        await ctx.reply(event, content)
        return EventResult.CONSUME
