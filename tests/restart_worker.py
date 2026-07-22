"""Helper process used by restart E2E tests."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.llm.invocation_store import InvocationStore  # noqa: E402
from src.core.runtime import (  # noqa: E402
    DurableRuntime,
    RecoveryController,
    RuntimeStore,
)


def create_interrupted(database: Path, state_file: Path, outcome: str) -> None:
    connection = sqlite3.connect(database)
    runtime = DurableRuntime(RuntimeStore(connection))
    invocations = InvocationStore(connection)
    task, run = runtime.create_task_run(
        goal=f"restart-{outcome}",
        trigger_type="manual",
        template={"kind": "restart_test"},
    )
    runtime.store.update_task(task.task_id, "RUNNING")
    runtime.store.update_run(run.run_id, "RUNNING")
    step = runtime.store.create_step(run.run_id, "tool", status="RUNNING")
    invocation = invocations.begin(
        tool_name="write_file",
        tool_version="1",
        arguments={"path": "result.txt"},
        run_id=run.run_id,
        task_id=task.task_id,
        step_id=step.step_id,
        invocation_id=None,
        idempotency_key="restart-write",
        trace_id="restart-e2e",
        user_id=1,
        group_id=2,
        config_snapshot_id="snapshot-1",
    )
    invocations.transition(invocation.invocation_id, "running")
    if outcome == "succeeded":
        invocations.transition(
            invocation.invocation_id,
            "succeeded",
            result={"artifact_id": "artifact-1"},
            terminal=True,
        )
    state_file.write_text(
        json.dumps({"task_id": task.task_id, "run_id": run.run_id, "step_id": step.step_id}),
        encoding="utf-8",
    )
    connection.close()


def recover(database: Path, state_file: Path) -> None:
    identifiers = json.loads(state_file.read_text(encoding="utf-8"))
    connection = sqlite3.connect(database)
    store = RuntimeStore(connection)
    runtime = DurableRuntime(store)
    recovered = RecoveryController(runtime, InvocationStore(connection)).recover()
    run = store.get_run(identifiers["run_id"])
    task = store.get_task(identifiers["task_id"])
    step = store.get_step(identifiers["step_id"])
    print(
        json.dumps(
            {
                "recovered": recovered,
                "run_status": run.status,
                "task_status": task.status,
                "step_status": step.status,
                "step_output": step.output(),
            }
        )
    )
    connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "recover"))
    parser.add_argument("database", type=Path)
    parser.add_argument("state_file", type=Path)
    parser.add_argument("--outcome", choices=("unknown", "succeeded"), default="unknown")
    args = parser.parse_args()
    if args.mode == "create":
        create_interrupted(args.database, args.state_file, args.outcome)
    else:
        recover(args.database, args.state_file)


if __name__ == "__main__":
    main()
