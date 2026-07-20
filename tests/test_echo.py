"""Tests for the echo plugin."""

from __future__ import annotations

import asyncio

from plugins.echo import echo
from tests.factories import make_event


class TestEcho:
    def test_echo_group_message_with_content(self, bot):
        event = make_event(message="/echo hello")
        result = asyncio.run(echo(event, bot))
        assert result is True
        assert len(bot.api.calls) == 1
        assert bot.api.calls[0]["method"] == "send_group_msg"
        assert bot.api.calls[0]["message"] == "hello"

    def test_echo_private_message(self, bot):
        event = make_event(
            type="message.private", message="/echo test", group_id=None, is_group=False
        )
        result = asyncio.run(echo(event, bot))
        assert result is True
        assert bot.api.calls[0]["method"] == "send_private_msg"
        assert bot.api.calls[0]["message"] == "test"

    def test_echo_no_content_group(self, bot):
        event = make_event(message="/echo")
        result = asyncio.run(echo(event, bot))
        assert result is True
        assert "Usage" in bot.api.calls[0]["message"]

    def test_echo_no_content_private(self, bot):
        event = make_event(
            type="message.private", message="/echo", group_id=None, is_group=False
        )
        result = asyncio.run(echo(event, bot))
        assert result is True
        assert bot.api.calls[0]["method"] == "send_private_msg"
        assert "Usage" in bot.api.calls[0]["message"]

    def test_echo_removes_prefix_and_trims(self, bot):
        event = make_event(message="/echo  hello world  ")
        result = asyncio.run(echo(event, bot))
        assert result is True
        assert bot.api.calls[0]["message"] == "hello world"

    def test_echo_returns_true(self, bot):
        event = make_event(message="/echo anything")
        result = asyncio.run(echo(event, bot))
        assert result is True
