from __future__ import annotations

import asyncio

from plugins.poke import poke_back
from src.plugin.base import Event


class TestPokeBack:
    def test_non_poke_sub_type_is_ignored(self, bot):
        event = Event(
            type="notice.notify",
            user_id=123,
            group_id=456,
            is_group=True,
            raw={"sub_type": "other", "user_id": 123, "target_id": 999},
        )
        result = asyncio.run(poke_back(event, bot))
        assert result is False
        assert bot.api.calls == []

    def test_follow_poke(self, bot):
        """Follow-poke: someone pokes someone else, bot pokes the target too."""
        event = Event(
            type="notice.notify",
            user_id=789,
            group_id=101112,
            is_group=True,
            raw={"sub_type": "poke", "user_id": 789, "target_id": 999},
        )
        result = asyncio.run(poke_back(event, bot))
        assert result is True
        assert bot.api.calls == [
            {"method": "send_group_poke", "group_id": 101112, "user_id": 999}
        ]

    def test_bot_poked_pokes_back_poker(self, bot):
        """When someone pokes the bot, bot pokes back the poker (not itself)."""
        event = Event(
            type="notice.notify",
            user_id=789,
            group_id=101112,
            is_group=True,
            raw={"sub_type": "poke", "user_id": 789, "target_id": bot.config.bot.qq},
        )
        result = asyncio.run(poke_back(event, bot))
        assert result is True
        assert bot.api.calls == [
            {"method": "send_group_poke", "group_id": 101112, "user_id": 789}
        ]

    def test_duplicate_poke_notice_is_ignored(self, bot):
        """Duplicate delivery of one poke should produce only one poke action."""
        event = Event(
            type="notice.notify",
            user_id=789,
            group_id=101113,
            is_group=True,
            raw={
                "time": 1784827566,
                "sub_type": "poke",
                "user_id": 789,
                "target_id": bot.config.bot.qq,
            },
        )

        first = asyncio.run(poke_back(event, bot))
        duplicate = asyncio.run(poke_back(event, bot))

        assert first is True
        assert duplicate is True
        assert bot.api.calls == [
            {"method": "send_group_poke", "group_id": 101113, "user_id": 789}
        ]

    def test_self_poke_is_ignored(self, bot):
        """Bot's own pokes should not trigger a reaction (infinite loop prevention)."""
        event = Event(
            type="notice.notify",
            user_id=bot.config.bot.qq,
            group_id=101112,
            is_group=True,
            raw={"sub_type": "poke", "user_id": bot.config.bot.qq, "target_id": 999},
        )
        result = asyncio.run(poke_back(event, bot))
        assert result is False
        assert bot.api.calls == []

    def test_private_poke_is_ignored(self, bot):
        event = Event(
            type="notice.notify",
            user_id=123,
            is_group=False,
            raw={"sub_type": "poke", "user_id": 123, "target_id": 999},
        )
        result = asyncio.run(poke_back(event, bot))
        assert result is False
        assert bot.api.calls == []
