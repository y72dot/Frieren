from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.core.runtime.store import RuntimeStore, TaskRecord, TaskRunRecord

RunHandler = Callable[[dict[str, Any], "RunContext"], Awaitable[Any]]


@dataclass(frozen=True)
class RunContext:
    task_id: str
    run_id: str
    step_id: str
    requested_by: int | None
    conversation_type: str | None
    conversation_id: int | None


class DurableRuntime:
    """Runs persisted task templates through explicitly registered handlers."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store
        self._handlers: dict[str, RunHandler] = {}
        self._background: set[asyncio.Task[Any]] = set()

    def register_handler(self, kind: str, handler: RunHandler) -> None:
        self._handlers[kind] = handler

    def create_task_run(
        self,
        *,
        goal: str,
        trigger_type: str,
        template: dict[str, Any],
        requested_by: int | None = None,
        conversation_type: str | None = None,
        conversation_id: int | None = None,
        trigger_event_id: str | None = None,
        config_snapshot_id: str = "",
        prompt_version: str = "",
        scheduled: bool = False,
    ) -> tuple[TaskRecord, TaskRunRecord]:
        status = "SCHEDULED" if scheduled else "CREATED"
        task = self.store.create_task(
            goal,
            trigger_type=trigger_type,
            trigger_event_id=trigger_event_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            requested_by=requested_by,
            metadata={"template": template},
            status=status,
        )
        run = self.store.create_run(
            task.task_id,
            status=status,
            config_snapshot_id=config_snapshot_id,
            prompt_version=prompt_version,
        )
        return task, run

    async def execute_run(self, run_id: str) -> Any:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        task = self.store.get_task(run.task_id)
        if task is None:
            raise KeyError(f"task not found: {run.task_id}")
        template = task.metadata().get("template", {})
        kind = str(template.get("kind", ""))
        handler = self._handlers.get(kind)
        if handler is None:
            error = f"no runtime handler registered for: {kind}"
            self.store.update_run(run_id, "FAILED", error=error)
            self.store.update_task(task.task_id, "FAILED", error=error)
            raise RuntimeError(error)

        self.store.update_task(task.task_id, "RUNNING")
        self.store.update_run(run_id, "RUNNING")
        step = self.store.create_step(run_id, kind, input_data=template, status="RUNNING")
        context = RunContext(
            task_id=task.task_id,
            run_id=run_id,
            step_id=step.step_id,
            requested_by=task.requested_by,
            conversation_type=task.conversation_type,
            conversation_id=task.conversation_id,
        )
        try:
            output = await handler(template, context)
        except asyncio.CancelledError:
            self.store.update_step(step.step_id, "CANCELLED", error="cancelled")
            self.store.update_run(run_id, "CANCELLED", error="cancelled")
            self.store.update_task(task.task_id, "CANCELLED", error="cancelled")
            raise
        except Exception as exc:
            logger.opt(exception=True).error(f"Durable run {run_id} failed: {exc}")
            self.store.update_step(step.step_id, "FAILED", error=str(exc))
            self.store.update_run(run_id, "FAILED", error=str(exc))
            self.store.update_task(task.task_id, "FAILED", error=str(exc))
            raise
        self.store.update_step(step.step_id, "SUCCEEDED", output=output)
        self.store.update_run(run_id, "SUCCEEDED")
        self.store.update_task(task.task_id, "SUCCEEDED")
        return output

    def submit(self, run_id: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(self.execute_run(run_id), name=f"agent-run:{run_id}")
        self._background.add(task)
        task.add_done_callback(self._on_background_done)
        return task

    def _on_background_done(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if not task.cancelled():
            task.exception()

    def wait(self, run_id: str, reason: str, resume_token: str) -> None:
        status = {
            "tool": "WAITING_TOOL",
            "artifact": "WAITING_ARTIFACT",
            "approval": "WAITING_APPROVAL",
            "user": "WAITING_USER",
        }.get(reason)
        if status is None:
            raise ValueError(f"unsupported wait reason: {reason}")
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        self.store.update_run(run_id, status, resume_token=resume_token)
        self.store.update_task(run.task_id, status)

    def resume(self, run_id: str, resume_token: str) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if not run.status.startswith("WAITING_"):
            raise ValueError(f"run is not waiting: {run.status}")
        if run.resume_token != resume_token:
            raise PermissionError("resume token does not match")
        self.store.update_run(run_id, "CREATED")
        self.store.update_task(run.task_id, "CREATED")

    def cancel(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        self.store.update_run(run_id, "CANCELLED", error="cancelled")
        self.store.update_task(run.task_id, "CANCELLED", error="cancelled")

    async def shutdown(self) -> None:
        for task in tuple(self._background):
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)


class RecoveryController:
    """Classify interrupted durable runs without guessing side effects."""

    def __init__(
        self, runtime: DurableRuntime, invocation_store: Any = None, catalog: Any = None
    ) -> None:
        self.runtime = runtime
        self.invocation_store = invocation_store
        self.catalog = catalog

    def recover(self) -> list[str]:
        recovered: list[str] = []
        for run in self.runtime.store.list_active_runs():
            steps = self.runtime.store.list_steps(run.run_id)
            running = next((step for step in reversed(steps) if step.status == "RUNNING"), None)
            invocations = (
                self.invocation_store.list_for_run(run.run_id)
                if self.invocation_store is not None
                else []
            )
            if running is not None and invocations:
                latest = invocations[-1]
                if latest.status == "succeeded":
                    self.runtime.store.update_step(
                        running.step_id, "SUCCEEDED", output=latest.result()
                    )
                    self.runtime.store.update_run(run.run_id, "CREATED")
                    self.runtime.store.update_task(run.task_id, "CREATED")
                    recovered.append(run.run_id)
                    continue
                tool = self.catalog.get(latest.tool_name) if self.catalog is not None else None
                safe_to_retry = latest.status == "validating" or (
                    latest.status == "running"
                    and tool is not None
                    and tool.risk_level.value == "read_only"
                )
                if safe_to_retry:
                    self.invocation_store.transition(
                        latest.invocation_id,
                        "failed",
                        error="interrupted by process restart",
                        terminal=True,
                    )
                    self.runtime.store.update_run(run.run_id, "CREATED")
                    self.runtime.store.update_task(run.task_id, "CREATED")
                    recovered.append(run.run_id)
                    continue
                if latest.status in {"running", "timed_out"}:
                    self.runtime.store.update_run(
                        run.run_id,
                        "WAITING_APPROVAL",
                        error="tool outcome unknown after restart",
                    )
                    self.runtime.store.update_task(run.task_id, "WAITING_APPROVAL")
                    recovered.append(run.run_id)
                    continue
            if run.status in {"CREATED", "SCHEDULED"}:
                recovered.append(run.run_id)
            elif run.status == "RUNNING":
                self.runtime.store.update_run(run.run_id, "CREATED")
                self.runtime.store.update_task(run.task_id, "CREATED")
                recovered.append(run.run_id)
        return recovered
