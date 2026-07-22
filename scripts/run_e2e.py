"""Run the release E2E matrix and emit a machine-readable report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]

LEVELS: dict[str, list[str]] = {
    "L0": [
        "tests/test_config.py",
        "tests/test_config_center.py",
        "tests/test_docker_contract.py",
        "tests/test_prompt_registry.py",
        "tests/test_qq_adapter.py",
    ],
    "L1": [
        "tests/test_message_ingestion.py",
        "tests/test_artifact_store.py",
        "tests/test_artifact_service.py",
        "tests/test_history_sync.py",
        "tests/test_history_query.py",
    ],
    "L2": [
        "tests/test_bus_integration.py",
        "tests/test_bot_lifecycle.py",
        "tests/test_history_bot_integration.py",
        "tests/test_full_pipeline.py",
    ],
    "L3": [
        "tests/test_tool_platform.py",
        "tests/test_capability_tools.py",
        "tests/test_control_plane.py",
        "tests/test_durable_runtime.py",
        "tests/test_scheduler.py",
    ],
    "L4": [
        "tests/test_e2e_pipeline.py",
        "tests/test_e2e_filters.py",
        "tests/test_e2e_llm_chain.py",
        "tests/test_e2e_multiturn.py",
        "tests/test_e2e_tools.py",
        "tests/test_e2e_errors.py",
        "tests/test_e2e_runtime.py",
        "tests/test_e2e_scenarios.py",
    ],
    "L5": [
        "tests/test_e2e_restart.py",
        "tests/test_safe_web.py",
        "tests/test_workspace_search.py",
        "tests/test_health.py",
    ],
    "L6": ["tests/test_live_napcat.py"],
}


def run_level(level: str) -> dict[str, object]:
    started = time.monotonic()
    base_temp = PROJECT_ROOT / "data" / "test-tmp" / f"{level.lower()}-{os.getpid()}"
    cache_dir = PROJECT_ROOT / "data" / "test-cache" / level.lower()
    base_temp.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--basetemp",
        str(base_temp),
        "-o",
        f"cache_dir={cache_dir}",
        *LEVELS[level],
    ]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    output = result.stdout + result.stderr
    print(f"\n===== {level} =====")
    print(output.rstrip())
    return {
        "level": level,
        "passed": result.returncode == 0,
        "returncode": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "command": command,
        "output_tail": output[-4000:],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="L0,L1,L2,L3,L4,L5")
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--report", default="data/test-reports/e2e-report.json")
    args = parser.parse_args()
    if args.list:
        print(json.dumps(LEVELS, ensure_ascii=False, indent=2))
        return

    selected = [item.strip().upper() for item in args.levels.split(",") if item.strip()]
    if args.require_live and "L6" not in selected:
        selected.append("L6")
    unknown = [item for item in selected if item not in LEVELS]
    if unknown:
        parser.error(f"unknown E2E levels: {', '.join(unknown)}")

    started_at = time.time()
    results: list[dict[str, object]] = []
    if args.require_live and os.getenv("QQBOT_LIVE") != "1":
        results.append(
            {
                "level": "L6",
                "passed": False,
                "returncode": 2,
                "duration_seconds": 0,
                "output_tail": "QQBOT_LIVE=1 is required by the release gate",
            }
        )
        selected = [item for item in selected if item != "L6"]
    for level in selected:
        results.append(run_level(level))

    report = {
        "schema_version": 1,
        "started_at": started_at,
        "duration_seconds": round(time.time() - started_at, 3),
        "passed": all(bool(item["passed"]) for item in results),
        "levels": results,
        "live_required": args.require_live,
    }
    report_path = PROJECT_ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nE2E report: {report_path}")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
