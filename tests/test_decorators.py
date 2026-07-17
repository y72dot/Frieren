"""Tests for decorator-based plugin creation."""

import re

import pytest

from src.plugin.base import Event
from src.plugin.decorators import command, on_keyword, on_notice, on_regex


# -------------------------------------------------------------------
# @command
# -------------------------------------------------------------------


async def _ping_handler(event: Event, bot) -> bool:
    return True


def test_command_decorator_sets_plugin():
    plugin = command("/ping")(_ping_handler)
    p = plugin.__plugin__
    assert p.name == "_ping_handler"
    assert p.priority == 0
    assert p.match(Event(type="message.group", user_id=1, message="/ping", is_group=True))
    assert p.match(Event(type="message.group", user_id=1, message="/ping 123", is_group=True))
    assert not p.match(Event(type="message.group", user_id=1, message="ping", is_group=True))
    assert not p.match(Event(type="message.group", user_id=1, message="/pong", is_group=True))


def test_command_multi_cmd():
    plugin = command(["/weather", "/天气"])(_ping_handler)
    p = plugin.__plugin__
    assert p.match(Event(type="message.group", user_id=1, message="/天气 北京"))
    assert p.match(Event(type="message.group", user_id=1, message="/weather shanghai"))


# -------------------------------------------------------------------
# @on_regex
# -------------------------------------------------------------------


async def _url_handler(event: Event, bot, match: re.Match) -> bool:
    return True


def test_regex_decorator_sets_plugin():
    plugin = on_regex(r"^(https?://[^\s]+)")(_url_handler)
    p = plugin.__plugin__
    assert p.name == "_url_handler"
    assert p.priority == 5
    assert p.match(Event(type="message.group", user_id=1, message="https://example.com"))
    assert not p.match(Event(type="message.group", user_id=1, message="see http://foo.bar/baz"))
    assert not p.match(Event(type="message.group", user_id=1, message="no url here"))


# -------------------------------------------------------------------
# @on_keyword
# -------------------------------------------------------------------


async def _greet_handler(event: Event, bot) -> bool:
    return True


def test_keyword_decorator_sets_plugin():
    plugin = on_keyword(["早安", "早上好"])(_greet_handler)
    p = plugin.__plugin__
    assert p.name == "_greet_handler"
    assert p.priority == 10
    assert p.match(Event(type="message.group", user_id=1, message="大家早安呀"))
    assert p.match(Event(type="message.group", user_id=1, message="早上好"))
    assert not p.match(Event(type="message.group", user_id=1, message="晚安"))


# -------------------------------------------------------------------
# @on_notice
# -------------------------------------------------------------------


async def _notice_handler(event: Event, bot) -> bool:
    return True


def test_notice_decorator_sets_plugin():
    plugin = on_notice("group_increase")(_notice_handler)
    p = plugin.__plugin__
    assert p.name == "_notice_handler"
    assert p.priority == 0
    assert p.match(Event(type="notice.group_increase", user_id=123, group_id=456, is_group=True))
    assert not p.match(Event(type="notice.group_decrease", user_id=123))
