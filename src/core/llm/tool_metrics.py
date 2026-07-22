"""Small in-process metrics collector for the LLM tool platform."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolMetricsSnapshot:
    registered: int
    views: int
    visible_total: int
    schema_bytes_total: int
    calls: int
    executions: int
    selection_hits: int
    tool_call_turns: int
    first_selection_hits: int
    denied: int
    unknown: int

    @property
    def average_visible(self) -> float:
        return self.visible_total / self.views if self.views else 0.0

    @property
    def average_schema_bytes(self) -> float:
        return self.schema_bytes_total / self.views if self.views else 0.0

    @property
    def selection_hit_rate(self) -> float:
        return self.selection_hits / self.calls if self.calls else 0.0

    @property
    def first_selection_hit_rate(self) -> float:
        if not self.tool_call_turns:
            return 0.0
        return self.first_selection_hits / self.tool_call_turns

    @property
    def average_calls_per_view(self) -> float:
        return self.calls / self.views if self.views else 0.0

    @property
    def denied_rate(self) -> float:
        return self.denied / self.executions if self.executions else 0.0

    @property
    def unknown_rate(self) -> float:
        return self.unknown / self.executions if self.executions else 0.0


class ToolMetrics:
    """Collect aggregate counters without coupling tools to a metrics backend."""

    def __init__(self, *, registered: int = 0) -> None:
        self.registered = registered
        self.views = 0
        self.visible_total = 0
        self.schema_bytes_total = 0
        self.calls = 0
        self.executions = 0
        self.selection_hits = 0
        self.tool_call_turns = 0
        self.first_selection_hits = 0
        self.denied = 0
        self.unknown = 0

    def record_view(
        self, *, registered: int, visible: int, schema_bytes: int
    ) -> None:
        self.registered = registered
        self.views += 1
        self.visible_total += visible
        self.schema_bytes_total += schema_bytes

    def record_tool_calls(self, names: list[str], visible_names: set[str]) -> None:
        self.calls += len(names)
        self.selection_hits += sum(name in visible_names for name in names)
        if names:
            self.tool_call_turns += 1
            self.first_selection_hits += names[0] in visible_names

    def record_execution(self) -> None:
        self.executions += 1

    def record_denied(self) -> None:
        self.denied += 1

    def record_unknown(self) -> None:
        self.unknown += 1

    def snapshot(self) -> ToolMetricsSnapshot:
        return ToolMetricsSnapshot(
            registered=self.registered,
            views=self.views,
            visible_total=self.visible_total,
            schema_bytes_total=self.schema_bytes_total,
            calls=self.calls,
            executions=self.executions,
            selection_hits=self.selection_hits,
            tool_call_turns=self.tool_call_turns,
            first_selection_hits=self.first_selection_hits,
            denied=self.denied,
            unknown=self.unknown,
        )
