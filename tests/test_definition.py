"""P1 PLUG-103: PluginDefinition and handler spec tests."""

from __future__ import annotations

import pytest

from src.plugin.definition import (
    CommandSpec,
    EventHandlerSpec,
    InternalHandlerSpec,
    LifecycleHookSpec,
    ObserverSpec,
    PluginDefinition,
    collect_definition,
    command,
    extract_definition,
    observe,
    on_event,
    on_internal,
    on_setup,
    on_start,
    on_stop,
)

# ---------------------------------------------------------------------------
# handler spec immutability
# ---------------------------------------------------------------------------


class TestHandlerSpecs:
    def test_command_spec_immutable(self):
        async def h(): pass
        spec = CommandSpec(name="test", aliases=("t",), priority=5, handler=h)
        assert spec.name == "test"
        assert spec.aliases == ("t",)
        assert spec.priority == 5
        with pytest.raises(Exception):
            spec.name = "other"  # type: ignore[misc]

    def test_event_handler_spec_immutable(self):
        async def h(): pass
        spec = EventHandlerSpec(event_type="message.group", priority=10, handler=h)
        assert spec.event_type == "message.group"
        assert spec.priority == 10
        with pytest.raises(Exception):
            spec.event_type = "changed"  # type: ignore[misc]

    def test_observer_spec_immutable(self):
        async def h(): pass
        spec = ObserverSpec(event_type="notice.notify", handler=h)
        assert spec.event_type == "notice.notify"
        with pytest.raises(Exception):
            spec.event_type = "other"  # type: ignore[misc]

    def test_internal_handler_spec_immutable(self):
        async def h(): pass
        spec = InternalHandlerSpec(message_type="internal", topic="metrics", handler=h)
        assert spec.message_type == "internal"
        assert spec.topic == "metrics"
        with pytest.raises(Exception):
            spec.topic = "changed"  # type: ignore[misc]

    def test_lifecycle_hook_spec_immutable(self):
        async def h(): pass
        spec = LifecycleHookSpec(hook_type="start", handler=h)
        assert spec.hook_type == "start"
        with pytest.raises(Exception):
            spec.hook_type = "stop"  # type: ignore[misc]

    def test_handlers_excluded_from_equality(self):
        async def h1(): pass
        async def h2(): pass
        s1 = CommandSpec(name="cmd", handler=h1)
        s2 = CommandSpec(name="cmd", handler=h2)
        assert s1 == s2  # different handlers, same name → equal

    def test_specs_with_different_names_not_equal(self):
        async def h(): pass
        s1 = CommandSpec(name="a", handler=h)
        s2 = CommandSpec(name="b", handler=h)
        assert s1 != s2


# ---------------------------------------------------------------------------
# PluginDefinition
# ---------------------------------------------------------------------------


class TestPluginDefinition:
    def test_empty_definition(self):
        d = PluginDefinition(plugin_id="test", version="1.0.0")
        assert d.plugin_id == "test"
        assert d.version == "1.0.0"
        assert d.commands == ()
        assert d.event_handlers == ()
        assert d.observers == ()
        assert d.internal_handlers == ()
        assert d.lifecycle_hooks == ()

    def test_definition_immutable(self):
        d = PluginDefinition(plugin_id="test", version="1.0.0")
        with pytest.raises(Exception):
            d.plugin_id = "other"  # type: ignore[misc]

    def test_definition_with_commands(self):
        async def h(): pass
        d = PluginDefinition(
            plugin_id="test",
            version="1.0.0",
            commands=(CommandSpec(name="hello", handler=h),),
        )
        assert len(d.commands) == 1
        assert d.commands[0].name == "hello"

    def test_definition_with_description(self):
        d = PluginDefinition(
            plugin_id="test", version="1.0.0", description="A test plugin"
        )
        assert d.description == "A test plugin"


# ---------------------------------------------------------------------------
# extract_definition – new-style
# ---------------------------------------------------------------------------


class TestExtractDefinition:
    def test_new_style_with_prebuilt_definition(self):
        async def h(): pass
        obj = type(
            "FakePlugin",
            (),
            {
                "__plugin_definition__": {
                    "commands": (CommandSpec(name="cmd", handler=h),),
                    "event_handlers": (),
                    "observers": (),
                    "internal_handlers": (),
                    "lifecycle_hooks": (),
                }
            },
        )
        d = extract_definition(obj, "test", "1.0.0")
        assert d.plugin_id == "test"
        assert d.version == "1.0.0"
        assert len(d.commands) == 1
        assert d.commands[0].name == "cmd"

    def test_random_object_returns_empty_definition(self):
        d = extract_definition(42, "nope")
        assert d.plugin_id == "nope"
        assert d.commands == ()
        assert d.event_handlers == ()


# ---------------------------------------------------------------------------
# stackable decorators
# ---------------------------------------------------------------------------


class TestStackableDecorators:
    def test_command_decorator_sets_metadata(self):
        @command("hello", aliases=["hi"], priority=5)
        async def hello_cmd(ctx, event, args):
            pass

        assert hasattr(hello_cmd, "__command_spec__")
        spec = hello_cmd.__command_spec__  # type: ignore[attr-defined]
        assert spec["name"] == "hello"
        assert spec["aliases"] == ("hi",)
        assert spec["priority"] == 5

    def test_command_decorator_without_aliases(self):
        @command("ping")
        async def ping_cmd(ctx, event, args):
            pass

        spec = ping_cmd.__command_spec__  # type: ignore[attr-defined]
        assert spec["aliases"] == ()

    def test_on_event_decorator_sets_metadata(self):
        @on_event("message.group", priority=20)
        async def handler(ctx, event):
            pass

        spec = handler.__event_handler_spec__  # type: ignore[attr-defined]
        assert spec["event_type"] == "message.group"
        assert spec["priority"] == 20

    def test_observe_decorator_sets_metadata(self):
        @observe("notice.notify")
        async def obs(ctx, event):
            pass

        spec = obs.__observer_spec__  # type: ignore[attr-defined]
        assert spec["event_type"] == "notice.notify"

    def test_on_internal_decorator_sets_metadata(self):
        @on_internal(topic="metrics")
        async def int_handler(ctx, payload):
            pass

        spec = int_handler.__internal_handler_spec__  # type: ignore[attr-defined]
        assert spec["topic"] == "metrics"

    def test_on_start_decorator_sets_metadata(self):
        @on_start
        async def start_hook(ctx):
            pass

        assert start_hook.__lifecycle_hook__ == "start"  # type: ignore[attr-defined]

    def test_on_stop_decorator_sets_metadata(self):
        @on_stop
        async def stop_hook(ctx):
            pass

        assert stop_hook.__lifecycle_hook__ == "stop"  # type: ignore[attr-defined]

    def test_on_setup_decorator_sets_metadata(self):
        @on_setup
        async def setup_hook(ctx):
            pass

        assert setup_hook.__lifecycle_hook__ == "setup"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# collect_definition
# ---------------------------------------------------------------------------


class TestCollectDefinition:
    def test_collects_commands_from_class(self):
        class MyPlugin:
            __plugin_id__ = "my_plugin"

            @command("hello", aliases=["hi"])
            async def hello_cmd(self, ctx, event, args):
                pass

        collect_definition(MyPlugin)
        assert hasattr(MyPlugin, "__plugin_definition__")
        raw = MyPlugin.__plugin_definition__  # type: ignore[attr-defined]
        assert len(raw["commands"]) == 1
        assert raw["commands"][0].name == "hello"

    def test_collects_event_handlers_from_class(self):
        class MyPlugin:
            __plugin_id__ = "ep"

            @on_event("message.group", priority=5)
            async def on_msg(self, ctx, event):
                pass

        collect_definition(MyPlugin)
        raw = MyPlugin.__plugin_definition__  # type: ignore[attr-defined]
        assert len(raw["event_handlers"]) == 1
        assert raw["event_handlers"][0].event_type == "message.group"

    def test_collects_observers_from_class(self):
        class MyPlugin:
            __plugin_id__ = "obs"

            @observe("notice.notify")
            async def watch(self, ctx, event):
                pass

        collect_definition(MyPlugin)
        raw = MyPlugin.__plugin_definition__  # type: ignore[attr-defined]
        assert len(raw["observers"]) == 1
        assert raw["observers"][0].event_type == "notice.notify"

    def test_collects_internal_handlers_from_class(self):
        class MyPlugin:
            __plugin_id__ = "int"

            @on_internal(topic="health")
            async def on_health(self, ctx, payload):
                pass

        collect_definition(MyPlugin)
        raw = MyPlugin.__plugin_definition__  # type: ignore[attr-defined]
        assert len(raw["internal_handlers"]) == 1

    def test_collects_lifecycle_hooks_from_class(self):
        class MyPlugin:
            __plugin_id__ = "lc"

            @on_start
            async def do_start(self, ctx):
                pass

            @on_stop
            async def do_stop(self, ctx):
                pass

        collect_definition(MyPlugin)
        raw = MyPlugin.__plugin_definition__  # type: ignore[attr-defined]
        hooks = raw["lifecycle_hooks"]
        assert len(hooks) == 2
        hook_types = {h.hook_type for h in hooks}
        assert hook_types == {"start", "stop"}

    def test_collects_mixed_handlers_from_class(self):
        class FullPlugin:
            __plugin_id__ = "full"
            __plugin_description__ = "A full plugin"

            @command("hello")
            async def hello(self, ctx, event, args):
                pass

            @on_event("message.private")
            async def on_pm(self, ctx, event):
                pass

            @on_start
            async def start(self, ctx):
                pass

        collect_definition(FullPlugin)
        raw = FullPlugin.__plugin_definition__  # type: ignore[attr-defined]
        assert len(raw["commands"]) == 1
        assert len(raw["event_handlers"]) == 1
        assert len(raw["lifecycle_hooks"]) == 1
        assert raw["description"] == "A full plugin"

    def test_collect_definition_with_instance(self):
        class MyPlugin:
            __plugin_id__ = "inst_collect"

            @command("test")
            async def test_cmd(self, ctx, event, args):
                pass

        instance = MyPlugin()
        result = collect_definition(instance)
        assert len(result["commands"]) == 1

    def test_duplicate_commands_definition_still_has_both(self):
        class DupPlugin:
            __plugin_id__ = "dup"

            @command("same")
            async def a(self, ctx, event, args):
                pass

            @command("same")
            async def b(self, ctx, event, args):
                pass

        result = collect_definition(DupPlugin)
        # Both commands are still collected (dedup is a warning, not an error).
        assert len(result["commands"]) == 2

    def test_empty_class_collects_empty_definition(self):
        class EmptyPlugin:
            __plugin_id__ = "empty"

        collect_definition(EmptyPlugin)
        raw = EmptyPlugin.__plugin_definition__  # type: ignore[attr-defined]
        assert raw["commands"] == ()
        assert raw["event_handlers"] == ()
        assert raw["observers"] == ()
        assert raw["internal_handlers"] == ()
        assert raw["lifecycle_hooks"] == ()
