"""Poke package plugin – pokes back when poked in a group."""

from plugins.poke.plugin import PokePlugin  # noqa: F401

_instance = PokePlugin()


class _CompatCtx:
    """Minimal PluginContext compat layer for tests."""

    def __init__(self, bot):
        self.api = bot.api
        self.config = _CompatCfg(bot)

    async def reply(self, event, text) -> bool:
        if self.api is None:
            return False
        if getattr(event, "group_id", None) is not None:
            result = await self.api.send_group_msg(event.group_id, text)
        else:
            result = await self.api.send_private_msg(event.user_id, text)
        return result is not None


class _CompatCfg:
    def __init__(self, bot):
        self.bot_id = bot.config.bot.qq


async def poke_back(event, bot) -> bool:
    """Legacy-compatible wrapper: (Event, Bot) → bool."""
    ctx = _CompatCtx(bot)
    result = await _instance.poke_back(event, ctx)
    from src.plugin import EventResult  # noqa: PLC0415
    return result == EventResult.CONSUME
