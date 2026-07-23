"""QQBot Plugin SDK – public types and constants."""

SDK_VERSION = "1.0.0"

from src.plugin.base import Event  # noqa: E402, F401
from src.plugin.command import CommandMatch, CommandRegistry  # noqa: E402, F401
from src.plugin.config import (  # noqa: E402, F401
    PluginConfigError,
    PluginConfigSchema,
    build_plugin_config,
    load_schema,
)
from src.plugin.context import (  # noqa: E402, F401
    PermissionDeniedError,
    PluginConfigView,
    PluginContext,
    QQAgency,
)
from src.plugin.definition import (  # noqa: E402, F401
    CommandSpec,
    EventHandlerSpec,
    EventResult,
    InternalHandlerSpec,
    LifecycleHookSpec,
    ObserverSpec,
    PluginDefinition,
    collect_definition,
    command,
    observe,
    on_event,
    on_internal,
    on_setup,
    on_start,
    on_stop,
)
from src.plugin.runtime import PluginRuntime  # noqa: E402, F401
