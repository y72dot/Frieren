"""LLM Sender package – formats, chunks, and sends LLM replies."""

from plugins.llm_sender.plugin import (  # noqa: F401
    _QQ_MSG_LIMIT,
    LlmSenderPlugin,
    _split_message,
)

_instance = LlmSenderPlugin()


class _CompatCtx:
    """Minimal PluginContext compat layer – adapts raw Bot for legacy handler calls."""

    def __init__(self, bot):
        self.api = getattr(bot, "api", None)
        self._bot = bot
        bot_cfg = getattr(bot, "config", None)
        if bot_cfg is not None:
            bot_section = getattr(bot_cfg, "bot", None)
            self.config = _CompatCfg(bot_section)
        else:
            self.config = _CompatCfg(None)
        self._msg_store = getattr(bot, "msg_store", None)

    def record_bot_message(
        self, message_id, group_id, user_id, nickname, content, time, is_group, peer_id=None
    ):
        if self._msg_store is not None:
            self._msg_store.record_bot_message(
                message_id=message_id,
                group_id=group_id,
                user_id=user_id,
                nickname=nickname,
                content=content,
                time=time,
                is_group=is_group,
                peer_id=peer_id,
            )


class _CompatCfg:
    def __init__(self, bot_section):
        if bot_section is None:
            self.bot_id = 0
            self.nickname = "bot"
        else:
            self.bot_id = getattr(bot_section, "qq", 0)
            nicknames = getattr(bot_section, "nickname", ["bot"])
            self.nickname = nicknames[0] if nicknames else "bot"


async def llm_sender_handler(payload, bot) -> bool:
    """Legacy-compatible (payload, bot) → bool wrapper."""
    ctx = _CompatCtx(bot)
    result = await _instance.handle_send(payload, ctx)
    return result.value == "CONSUME" if hasattr(result, "value") else bool(result)
