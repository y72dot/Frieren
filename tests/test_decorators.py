"""Tests for decorator-based plugin creation."""

import re

from src.core.message_bus import MessageBus, MessageType
from src.plugin.base import Event
from src.plugin.decorators import command, on_keyword, on_notice, on_regex, subscribe

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


# -------------------------------------------------------------------
# @subscribe
# -------------------------------------------------------------------


async def _bus_handler(msg, bot) -> bool:
    return True


def test_subscribe_decorator_sets_metadata():
    """@subscribe attaches __subscribe__ metadata to the function."""
    wrapped = subscribe(MessageType.INTERNAL, priority=10)(_bus_handler)
    msg_type, priority = wrapped.__subscribe__  # type: ignore[attr-defined]
    assert msg_type == MessageType.INTERNAL
    assert priority == 10


def test_subscribe_auto_discover_registers_on_bus(tmp_path, monkeypatch):
    """A @subscribe handler module should be discovered and registered."""
    import sys

    from src.plugin.manager import PluginManager

    bus = MessageBus()
    pm = PluginManager(bus=bus)

    # Use a unique package name to avoid conflicts with project plugins/.
    plugin_dir = tmp_path / "testplugins"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("")

    code = '''
from src.plugin.decorators import subscribe
from src.core.message_bus import MessageType

@subscribe(MessageType.LIFECYCLE, priority=5)
async def on_startup(msg, bot) -> bool:
    return False
'''
    (plugin_dir / "lifecycle_plugin.py").write_text(code)

    sys.path.insert(0, str(tmp_path))
    try:
        count = pm.auto_discover([str(plugin_dir)])
    finally:
        sys.path.remove(str(tmp_path))

    assert count == 1
    assert pm.plugins[0].name == "on_startup"
    assert pm.plugins[0].priority == 5
