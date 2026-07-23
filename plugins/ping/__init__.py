"""Ping package plugin – responds to /ping with Pong!"""

from plugins.ping.plugin import PingPlugin  # noqa: F401

_instance = PingPlugin()


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


async def ping(event, bot) -> bool:
    """Legacy-compatible wrapper: (Event, Bot) → bool."""
    ctx = _CompatCtx(bot)
    result = await _instance.ping_cmd(event, ctx)
    from src.plugin.definition import EventResult  # noqa: PLC0415
    return result == EventResult.CONSUME
