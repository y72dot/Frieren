"""Data-driven in-process E2E scenario runner.

The runner deliberately consumes raw NapCat dictionaries.  It does not
normalize CQ codes before they enter EventBus, so scenarios exercise the same
lossless ingestion boundary as production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.llm import LlmResponse, ToolCall
from tests.conftest import FakeLlmProvider
from tests.conftest_e2e import assert_api_called, dispatch_raw_event


def load_scenario(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        scenario = json.load(handle)
    if not isinstance(scenario, dict) or not scenario.get("name"):
        raise ValueError(f"invalid E2E scenario: {path}")
    return scenario


class ScenarioRunner:
    def __init__(self, bot: Any, workspace_root: Path) -> None:
        self.bot = bot
        self.workspace_root = workspace_root

    async def run(self, scenario: dict[str, Any]) -> None:
        self._configure(scenario)
        for action in scenario.get("actions", []):
            if set(action) != {"dispatch"}:
                raise ValueError(f"unsupported scenario action: {action}")
            await dispatch_raw_event(self.bot, action["dispatch"])
        self._assertions(scenario.get("expect", {}))

    def _configure(self, scenario: dict[str, Any]) -> None:
        self.bot.config.workspace.root_dir = str(self.workspace_root)
        self.bot.workspace = None
        self.bot.search_service = None
        self.bot.ensure_capability_services()
        responses = scenario.get("llm", {}).get("responses", [])
        if responses:
            provider = FakeLlmProvider()
            provider.responses = [self._response(item) for item in responses]
            self.bot.llm_provider = provider

    @staticmethod
    def _response(item: dict[str, Any]) -> LlmResponse:
        calls = [
            ToolCall(
                id=str(call["id"]),
                name=str(call["name"]),
                arguments=dict(call.get("arguments", {})),
            )
            for call in item.get("tool_calls", [])
        ]
        return LlmResponse(text=str(item.get("text", "")), tool_calls=calls)

    def _assertions(self, expected: dict[str, Any]) -> None:
        for call in expected.get("qq_calls", []):
            params = {key: value for key, value in call.items() if key != "method"}
            assert_api_called(self.bot, call["method"], **params)

        for item in expected.get("messages", []):
            record = self.bot.msg_store.get_message_record(int(item["message_id"]))
            assert record is not None
            for field in ("raw_message", "ingestion_source"):
                if field in item:
                    assert record[field] == item[field]
            if "message_array" in item:
                assert json.loads(record["message_array_json"]) == item["message_array"]
            if item.get("raw_event_contains"):
                raw = json.loads(record["raw_event_json"])
                for key, value in item["raw_event_contains"].items():
                    assert raw[key] == value

        for relative, content in expected.get("workspace_files", {}).items():
            assert (self.workspace_root / relative).read_text(encoding="utf-8") == content

        for item in expected.get("artifacts", []):
            row = self.bot.msg_store.connection.execute(
                "SELECT source_type, status FROM artifacts WHERE file_name=? "
                "ORDER BY rowid DESC LIMIT 1",
                (item["file_name"],),
            ).fetchone()
            assert row == (item["source_type"], item["status"])

        runtime = expected.get("runtime")
        if runtime:
            task = self.bot.msg_store.connection.execute(
                "SELECT task_id, status FROM tasks ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            assert task is not None and task[1] == runtime["task_status"]
            run = self.bot.msg_store.connection.execute(
                "SELECT run_id, status FROM task_runs WHERE task_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (task[0],),
            ).fetchone()
            assert run is not None and run[1] == runtime["run_status"]
            assert [item.tool_name for item in self.bot.invocation_store.list_for_run(run[0])] == runtime.get(
                "tool_names", []
            )

        proposal = expected.get("proposal")
        if proposal:
            rows = self.bot.control_plane.list_proposals(status=proposal["status"])
            assert rows
            latest = rows[-1]
            assert latest.kind == proposal["kind"]
            for path, value in proposal.get("effective_settings", {}).items():
                assert self.bot.config_center.get(path) == value
