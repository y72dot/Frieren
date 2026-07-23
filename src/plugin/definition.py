"""PluginDefinition, handler specifications, and stackable decorators.

A :class:`PluginDefinition` captures every handler a plugin declares
**after** its entrypoint is imported.  Plugins use stackable decorators
on class methods; :func:`collect_definition` aggregates them into a
structured, inspectable set of handler specs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# EventResult
# ---------------------------------------------------------------------------


class EventResult(Enum):
    """Return value for event handlers: CONSUME stops propagation, CONTINUE passes."""

    CONSUME = "consume"
    CONTINUE = "continue"

    @classmethod
    def from_bool(cls, value: bool) -> EventResult:
        return cls.CONSUME if value else cls.CONTINUE

    def to_bool(self) -> bool:
        return self == EventResult.CONSUME


# ---------------------------------------------------------------------------
# HandlerSpec dataclasses (frozen, handler excluded from hash/eq)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """A command handler registered by a plugin."""

    name: str
    handler: Callable[..., Any] = field(compare=False, hash=False, repr=False)
    aliases: tuple[str, ...] = ()
    priority: int = 0


@dataclass(frozen=True)
class EventHandlerSpec:
    """An event consumer handler for EXTERNAL events (suppressible)."""

    event_type: str  # e.g. "message.group", "notice.notify", or "*" for all
    priority: int
    handler: Callable[..., Any] = field(compare=False, hash=False, repr=False)


@dataclass(frozen=True)
class ObserverSpec:
    """A non-consuming observer of EXTERNAL events."""

    event_type: str
    handler: Callable[..., Any] = field(compare=False, hash=False, repr=False)


@dataclass(frozen=True)
class InternalHandlerSpec:
    """A handler for INTERNAL or LIFECYCLE messages."""

    message_type: str  # "internal" or "lifecycle"
    handler: Callable[..., Any] = field(compare=False, hash=False, repr=False)
    topic: str = ""


@dataclass(frozen=True)
class LifecycleHookSpec:
    """A lifecycle hook handler (setup, start, stop)."""

    hook_type: str  # "setup" | "start" | "stop"
    handler: Callable[..., Any] = field(compare=False, hash=False, repr=False)


# ---------------------------------------------------------------------------
# PluginDefinition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginDefinition:
    """Static capability set collected after importing a plugin module.

    Built once per plugin generation; consumed by
    :class:`PluginManager` to register handlers on the
    :class:`MessageBus`.
    """

    plugin_id: str
    version: str
    description: str = ""
    commands: tuple[CommandSpec, ...] = ()
    event_handlers: tuple[EventHandlerSpec, ...] = ()
    observers: tuple[ObserverSpec, ...] = ()
    internal_handlers: tuple[InternalHandlerSpec, ...] = ()
    lifecycle_hooks: tuple[LifecycleHookSpec, ...] = ()
    config_schema: str = ""


# ---------------------------------------------------------------------------
# extract_definition
# ---------------------------------------------------------------------------


def extract_definition(obj: Any, plugin_id: str, version: str = "0.0.0") -> PluginDefinition:
    """Build a :class:`PluginDefinition` from an imported object.

    Handles new-style objects (class or instance) carrying a
    ``__plugin_definition__`` attribute built by the stackable
    decorators from :func:`collect_definition`.
    """
    if hasattr(obj, "__plugin_definition__"):
        raw = obj.__plugin_definition__
        return PluginDefinition(
            plugin_id=plugin_id,
            version=version,
            description=raw.get("description", ""),
            commands=tuple(raw.get("commands", ())),
            event_handlers=tuple(raw.get("event_handlers", ())),
            observers=tuple(raw.get("observers", ())),
            internal_handlers=tuple(raw.get("internal_handlers", ())),
            lifecycle_hooks=tuple(raw.get("lifecycle_hooks", ())),
            config_schema=raw.get("config_schema", ""),
        )

    return PluginDefinition(plugin_id=plugin_id, version=version)


# ---------------------------------------------------------------------------
# new-style stackable decorators
# ---------------------------------------------------------------------------


def command(name: str, *, aliases: list[str] | None = None, priority: int = 0) -> Callable:
    """Mark a method as a command handler.

    Usage::

        class MyPlugin:
            __plugin_id__ = "my_plugin"

            @command("hello", aliases=["你好"])
            async def hello_cmd(self, ctx, event, args):
                await ctx.reply("Hello!")
    """

    def decorator(func):
        func.__command_spec__ = {  # type: ignore[attr-defined]
            "name": name,
            "aliases": tuple(aliases or []),
            "priority": priority,
        }
        return func

    return decorator


def on_event(event_type: str, *, priority: int = 0) -> Callable:
    """Mark a method as an EXTERNAL event consumer."""

    def decorator(func):
        func.__event_handler_spec__ = {  # type: ignore[attr-defined]
            "event_type": event_type,
            "priority": priority,
        }
        return func

    return decorator


def observe(event_type: str) -> Callable:
    """Mark a method as a non-consuming EXTERNAL observer."""

    def decorator(func):
        func.__observer_spec__ = {"event_type": event_type}  # type: ignore[attr-defined]
        return func

    return decorator


def on_internal(topic: str = "") -> Callable:
    """Mark a method as an INTERNAL message handler."""

    def decorator(func):
        func.__internal_handler_spec__ = {"topic": topic}  # type: ignore[attr-defined]
        return func

    return decorator


def on_start(func=None):  # type: ignore
    """Mark a method as a ``start`` lifecycle hook."""
    if func is not None:
        func.__lifecycle_hook__ = "start"  # type: ignore[attr-defined]
        return func

    def decorator(f):
        f.__lifecycle_hook__ = "start"  # type: ignore[attr-defined]
        return f

    return decorator


def on_stop(func=None):  # type: ignore
    """Mark a method as a ``stop`` lifecycle hook."""
    if func is not None:
        func.__lifecycle_hook__ = "stop"  # type: ignore[attr-defined]
        return func

    def decorator(f):
        f.__lifecycle_hook__ = "stop"  # type: ignore[attr-defined]
        return f

    return decorator


def on_setup(func=None):  # type: ignore
    """Mark a method as a ``setup`` lifecycle hook."""
    if func is not None:
        func.__lifecycle_hook__ = "setup"  # type: ignore[attr-defined]
        return func

    def decorator(f):
        f.__lifecycle_hook__ = "setup"  # type: ignore[attr-defined]
        return f

    return decorator


# ---------------------------------------------------------------------------
# definition collector
# ---------------------------------------------------------------------------


def collect_definition(cls_or_instance) -> dict:
    """Aggregate decorated method metadata from a class or instance.

    Walks all methods looking for ``__command_spec__``,
    ``__event_handler_spec__``, etc., and builds the dictionary
    suitable for constructing a :class:`PluginDefinition`.

    Returns a dict with keys: ``commands``, ``event_handlers``,
    ``observers``, ``internal_handlers``, ``lifecycle_hooks``,
    ``description``, ``config_schema``.
    """
    obj = cls_or_instance() if isinstance(cls_or_instance, type) else cls_or_instance
    target = cls_or_instance if isinstance(cls_or_instance, type) else type(obj)

    commands: list[CommandSpec] = []
    event_handlers: list[EventHandlerSpec] = []
    observers: list[ObserverSpec] = []
    internal_handlers: list[InternalHandlerSpec] = []
    lifecycle_hooks: list[LifecycleHookSpec] = []

    for attr_name in dir(target):
        if attr_name.startswith("_"):
            continue
        attr = getattr(target, attr_name, None)
        if attr is None:
            continue

        # Bind to instance if it's a method.
        handler = getattr(obj, attr_name) if hasattr(obj, attr_name) else attr

        if hasattr(attr, "__command_spec__"):
            spec = attr.__command_spec__  # type: ignore[attr-defined]
            commands.append(
                CommandSpec(
                    name=spec["name"],
                    aliases=spec.get("aliases", ()),
                    priority=spec.get("priority", 0),
                    handler=handler,
                )
            )

        if hasattr(attr, "__event_handler_spec__"):
            spec = attr.__event_handler_spec__  # type: ignore[attr-defined]
            event_handlers.append(
                EventHandlerSpec(
                    event_type=spec["event_type"],
                    priority=spec.get("priority", 0),
                    handler=handler,
                )
            )

        if hasattr(attr, "__observer_spec__"):
            spec = attr.__observer_spec__  # type: ignore[attr-defined]
            observers.append(
                ObserverSpec(
                    event_type=spec["event_type"],
                    handler=handler,
                )
            )

        if hasattr(attr, "__internal_handler_spec__"):
            spec = attr.__internal_handler_spec__  # type: ignore[attr-defined]
            internal_handlers.append(
                InternalHandlerSpec(
                    message_type="internal",
                    topic=spec.get("topic", ""),
                    handler=handler,
                )
            )

        if hasattr(attr, "__lifecycle_hook__"):
            hook_type = attr.__lifecycle_hook__  # type: ignore[attr-defined]
            lifecycle_hooks.append(
                LifecycleHookSpec(
                    hook_type=hook_type,
                    handler=handler,
                )
            )

    # Detect duplicate commands within the same plugin.
    dup_errors = _detect_duplicate_commands(commands)
    for error in dup_errors:
        logger.warning(f"Plugin definition warning: {error}")

    # Attach the collected definition back to the target.
    definition = {
        "commands": tuple(commands),
        "event_handlers": tuple(event_handlers),
        "observers": tuple(observers),
        "internal_handlers": tuple(internal_handlers),
        "lifecycle_hooks": tuple(lifecycle_hooks),
        "description": getattr(target, "__plugin_description__", ""),
        "config_schema": getattr(target, "__plugin_config_schema__", ""),
    }

    # Stash on the class for __plugin_definition__ discovery.
    if isinstance(cls_or_instance, type):
        cls_or_instance.__plugin_definition__ = definition  # type: ignore[attr-defined]

    return definition


def _detect_duplicate_commands(specs: list[CommandSpec]) -> list[str]:
    """Check for duplicate command names or aliases within the same plugin."""
    errors: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            errors.append(f"Duplicate command name '{spec.name}'")
        seen.add(spec.name)
        for alias in spec.aliases:
            if alias in seen:
                errors.append(
                    f"Command alias '{alias}' conflicts with existing name or alias"
                )
            seen.add(alias)
    return errors
