"""Echo package plugin – echoes back the message following /echo."""

from plugins.echo.plugin import EchoPlugin  # noqa: F401

_instance = EchoPlugin()


class _CompatCtx:
    """Minimal PluginContext compat layer – forwards to bot.api for tests."""

    def __init__(self, bot):
        self.api = bot.api

    async def reply(self, event, text) -> bool:
        if self.api is None:
            return False
        if getattr(event, "group_id", None) is not None:
            result = await self.api.send_group_msg(event.group_id, text)
        else:
            result = await self.api.send_private_msg(event.user_id, text)
        return result is not None


async def echo(event, bot) -> bool:
    """Legacy-compatible wrapper: (Event, Bot) → bool."""
    ctx = _CompatCtx(bot)
    result = await _instance.echo_cmd(event, ctx)
    from src.plugin import EventResult  # noqa: PLC0415
    return result == EventResult.CONSUME
