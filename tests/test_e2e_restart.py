from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).with_name("restart_worker.py")


def _worker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKER), *args],
        cwd=Path(__file__).parents[1],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("outcome", "run_status", "step_status", "step_output"),
    [
        ("unknown", "WAITING_APPROVAL", "RUNNING", None),
        ("succeeded", "CREATED", "SUCCEEDED", {"artifact_id": "artifact-1"}),
    ],
)
def test_recovery_crosses_a_real_process_boundary(
    tmp_path, outcome, run_status, step_status, step_output
):
    database = tmp_path / f"{outcome}.db"
    state = tmp_path / f"{outcome}.json"

    _worker("create", str(database), str(state), "--outcome", outcome)
    result = _worker("recover", str(database), str(state))

    report = json.loads(result.stdout.strip())
    assert len(report["recovered"]) == 1
    assert report["run_status"] == run_status
    assert report["task_status"] == run_status
    assert report["step_status"] == step_status
    assert report["step_output"] == step_output
