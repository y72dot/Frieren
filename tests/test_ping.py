"""Tests for the ping plugin."""

from __future__ import annotations

import asyncio

from plugins.ping import ping
from tests.factories import make_event


class TestPing:
    def test_ping_group_responds_pong(self, bot):
        event = make_event(type="message.group", message="/ping", group_id=456, is_group=True)
        result = asyncio.run(ping(event, bot))
        assert result is True
        assert len(bot.api.calls) == 1
        assert bot.api.calls[0]["method"] == "send_group_msg"
        assert bot.api.calls[0]["message"] == "Pong!"
        assert bot.api.calls[0]["group_id"] == 456

    def test_ping_private_responds_pong(self, bot):
        event = make_event(type="message.private", message="/ping", group_id=None, is_group=False)
        result = asyncio.run(ping(event, bot))
        assert result is True
        assert len(bot.api.calls) == 1
        assert bot.api.calls[0]["method"] == "send_private_msg"
        assert bot.api.calls[0]["message"] == "Pong!"
        assert bot.api.calls[0]["user_id"] == 123

    def test_ping_always_returns_true(self, bot):
        event = make_event(message="/ping")
        result = asyncio.run(ping(event, bot))
        assert result is True
