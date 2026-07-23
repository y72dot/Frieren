"""LLM Gate package plugin – @bot detection, access control, and LLM trigger emission."""

from __future__ import annotations

from loguru import logger

from src.core.message_store import _extract_nickname
from src.plugin import EventResult, on_event


class LlmGatePlugin:
    __plugin_id__ = "llm_gate"
    name = "llm_gate"
    priority = 5

    # ------------------------------------------------------------------
    # new-style decorated handlers
    # ------------------------------------------------------------------

    @on_event("message.private", priority=5)
    async def handle_private(self, event, ctx) -> EventResult:
        return await self._handle(event, ctx)

    @on_event("message.group", priority=5)
    async def handle_group(self, event, ctx) -> EventResult:
        return await self._handle(event, ctx)

    # ------------------------------------------------------------------
    # legacy compat – match/handle for existing tests
    # ------------------------------------------------------------------

    def match(self, event) -> bool:
        """Legacy match – checks event type and @bot mention."""
        if event.type not in ("message.group", "message.private"):
            return False
        if event.is_group:
            # Default bot_id for tests; real one comes from context.
            bot_at = "[CQ:at,qq="
            return bot_at in event.message
        return True

    async def handle(self, event, bot) -> bool:
        """Legacy handle – adapts (event, bot) → ctx and delegates."""
        from src.plugin.context import PluginConfigView

        class _CompatCtx:
            def __init__(self, b):
                self._bot = b
                bot_cfg = getattr(b, "config", None)
                llm_enabled = False
                if bot_cfg is not None:
                    llm_cfg = getattr(bot_cfg, "llm", None)
                    if llm_cfg is not None:
                        llm_enabled = getattr(llm_cfg, "enabled", False)
                    bot_section = getattr(bot_cfg, "bot", None)
                    bot_id = getattr(bot_section, "qq", 0) if bot_section else 0
                else:
                    bot_id = 0
                self.config = PluginConfigView(
                    bot_id=bot_id, nickname="test", admin_users=(), llm_enabled=llm_enabled
                )
                if hasattr(b, "message_bus"):
                    self._message_bus = b.message_bus

            async def emit_internal_and_wait(self, topic, data):
                if hasattr(self, "_message_bus"):
                    from src.core.message_bus import BusMessage, MessageType
                    await self._message_bus.emit_and_wait(
                        BusMessage(
                            type=MessageType.INTERNAL,
                            payload={"topic": topic, **data},
                            source="llm_gate",
                        ),
                        self._bot,
                    )

        ctx = _CompatCtx(bot)
        result = await self._handle(event, ctx)
        from src.plugin import EventResult
        return result == EventResult.CONSUME

    async def _handle(self, event, ctx) -> EventResult:
        if not ctx.config.llm_enabled:
            logger.debug("llm_gate: LLM disabled in config, skipping")
            return EventResult.CONTINUE
        if ctx._bot.llm_provider is None:
            logger.debug("llm_gate: no LLM provider, skipping")
            return EventResult.CONTINUE
        if event.user_id == ctx.config.bot_id:
            logger.debug("llm_gate: self-message, skipping")
            return EventResult.CONTINUE

        # Group messages: only respond when this bot is specifically @mentioned
        if event.is_group:
            bot_at = f"[CQ:at,qq={ctx.config.bot_id}]"
            if bot_at not in event.message:
                logger.debug(
                    f"llm_gate: group message without @bot, skipping grp={event.group_id}"
                )
                return EventResult.CONTINUE

        # Strip whitespace only; keep CQ codes intact so the LLM can see them.
        plain = event.message.strip()
        if not plain:
            logger.debug("llm_gate: empty message, skipping")
            return EventResult.CONTINUE

        session_key = (
            f"group:{event.group_id}" if event.is_group else f"private:{event.user_id}"
        )
        nickname = _extract_nickname(event.raw, event.user_id)

        logger.info(
            f"llm_gate trigger: session={session_key} user={event.user_id} "
            f"nickname={nickname} text={plain[:80]}"
        )

        await ctx.emit_internal_and_wait(
            "trigger",
            {
                "llm_type": "trigger",
                "session_key": session_key,
                "user_id": event.user_id,
                "group_id": event.group_id,
                "is_group": event.is_group,
                "text": plain,
                "nickname": nickname,
                "message_id": event.message_id,
            },
        )
        return EventResult.CONSUME
