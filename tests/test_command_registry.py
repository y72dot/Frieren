"""Tests for CommandRegistry: match exact, match prefix, aliases, args extraction, conflicts, empty."""


from src.plugin.command import CommandRegistry
from src.plugin.definition import CommandSpec
from src.plugin.registry import RegistrySnapshot

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _noop_handler(*args, **kwargs):
    pass


def _make_snapshot(commands: dict[str, tuple[CommandSpec, str]] | None = None) -> RegistrySnapshot:
    if commands is None:
        commands = {}
    return RegistrySnapshot(
        generation=1,
        commands_by_name=commands,
        plugin_ids=frozenset(),
    )


# ---------------------------------------------------------------------------
# find() tests
# ---------------------------------------------------------------------------


class TestFind:
    def test_exact_match_returns_command_match_with_empty_args(self):
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        result = reg.find("ping")
        assert result is not None
        assert result.spec.name == "ping"
        assert result.plugin_id == "p1"
        assert result.args == ""

    def test_prefix_match_with_space_returns_args(self):
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        result = reg.find("ping hello world")
        assert result is not None
        assert result.args == "hello world"

    def test_prefix_match_with_newline_returns_args(self):
        cmd = CommandSpec(name="echo", handler=_noop_handler)
        snap = _make_snapshot({"echo": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        result = reg.find("echo\nline2")
        assert result is not None
        assert result.args == "line2"

    def test_no_match_returns_none(self):
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        assert reg.find("pong") is None

    def test_empty_message_returns_none(self):
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        assert reg.find("") is None

    def test_partial_prefix_no_match(self):
        """'pingx' should NOT match command 'ping'."""
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        assert reg.find("pingx") is None

    def test_command_with_args_preserves_full_remaining_text(self):
        cmd = CommandSpec(name="say", handler=_noop_handler)
        snap = _make_snapshot({"say": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        result = reg.find("say hello world from qq")
        assert result is not None
        assert result.args == "hello world from qq"


# ---------------------------------------------------------------------------
# alias tests
# ---------------------------------------------------------------------------


class TestAliases:
    def test_find_matches_alias(self):
        cmd = CommandSpec(name="hello", aliases=("hi",), handler=_noop_handler)
        snap = _make_snapshot({"hello": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        result = reg.find("hi")
        assert result is not None
        assert result.spec.name == "hello"  # canonical name
        assert result.plugin_id == "p1"

    def test_alias_with_args(self):
        cmd = CommandSpec(name="hello", aliases=("hi",), handler=_noop_handler)
        snap = _make_snapshot({"hello": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        result = reg.find("hi there")
        assert result is not None
        assert result.spec.name == "hello"
        assert result.args == "there"

    def test_canonical_name_wins_over_alias(self):
        """When both canonical name and alias could match, canonical wins (first checked)."""
        cmd = CommandSpec(name="hi", aliases=("hello",), handler=_noop_handler)
        snap = _make_snapshot({"hi": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        result = reg.find("hi")
        assert result is not None
        assert result.spec.name == "hi"


# ---------------------------------------------------------------------------
# CQ code tests
# ---------------------------------------------------------------------------


class TestCQStrip:
    def test_cq_codes_stripped_before_matching(self):
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        result = reg.find("[CQ:at,qq=123]ping")
        assert result is not None
        assert result.spec.name == "ping"

    def test_cq_code_only_returns_none(self):
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        assert reg.find("[CQ:at,qq=123]") is None


# ---------------------------------------------------------------------------
# list_all tests
# ---------------------------------------------------------------------------


class TestListAll:
    def test_list_all_returns_all_commands(self):
        cmd1 = CommandSpec(name="ping", handler=_noop_handler)
        cmd2 = CommandSpec(name="echo", aliases=("e",), handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd1, "p1"), "echo": (cmd2, "p2")})
        reg = CommandRegistry.from_snapshot(snap)
        all_cmds = reg.list_all()
        assert len(all_cmds) == 2
        names = {c[0] for c in all_cmds}
        assert names == {"ping", "echo"}

    def test_list_all_empty_registry(self):
        snap = _make_snapshot({})
        reg = CommandRegistry.from_snapshot(snap)
        assert reg.list_all() == []

    def test_list_all_includes_aliases(self):
        cmd = CommandSpec(name="hello", aliases=("hi", "hey"), handler=_noop_handler)
        snap = _make_snapshot({"hello": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        all_cmds = reg.list_all()
        assert len(all_cmds) == 1
        name, pid, aliases = all_cmds[0]
        assert name == "hello"
        assert pid == "p1"
        assert set(aliases) == {"hi", "hey"}


# ---------------------------------------------------------------------------
# from_snapshot / conflicts
# ---------------------------------------------------------------------------


class TestFromSnapshot:
    def test_from_snapshot_builds_correctly(self):
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        assert reg.find("ping") is not None

    def test_detect_conflicts_empty_when_no_conflicts(self):
        cmd = CommandSpec(name="ping", handler=_noop_handler)
        snap = _make_snapshot({"ping": (cmd, "p1")})
        reg = CommandRegistry.from_snapshot(snap)
        assert reg.detect_conflicts() == []
