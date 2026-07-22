"""Deterministic SQLite ingestion/search performance baseline."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger  # noqa: E402

from src.core.event_bus import EventBus  # noqa: E402
from src.core.message_store import MessageStore  # noqa: E402


def benchmark(*, messages: int, searches: int) -> dict[str, float | int]:
    logger.disable("src.core.event_bus")
    with tempfile.TemporaryDirectory() as directory:
        store = MessageStore(db_path=str(Path(directory) / "benchmark.db"))
        parser = EventBus()
        started = time.perf_counter()
        for number in range(messages):
            raw = {
                "post_type": "message",
                "message_type": "group",
                "message_id": 900000 + number,
                "group_id": 456,
                "user_id": number % 50 + 1,
                "time": 1784600000 + number,
                "raw_message": f"benchmark searchable token-{number % 10}",
                "message": [
                    {
                        "type": "text",
                        "data": {"text": f"benchmark searchable token-{number % 10}"},
                    }
                ],
                "sender": {"nickname": f"user-{number % 50}"},
            }
            event = parser.parse(raw)
            assert event is not None
            store.record(event)
        ingest_seconds = time.perf_counter() - started

        latencies: list[float] = []
        for number in range(searches):
            started = time.perf_counter()
            store.search(456, f"token-{number % 10}", n=20)
            latencies.append((time.perf_counter() - started) * 1000)
        store.close()

    ordered = sorted(latencies)
    p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    result = {
        "messages": messages,
        "searches": searches,
        "ingest_messages_per_second": round(messages / ingest_seconds, 2),
        "search_p50_ms": round(statistics.median(latencies), 3),
        "search_p95_ms": round(ordered[p95_index], 3),
    }
    logger.enable("src.core.event_bus")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=int, default=2000)
    parser.add_argument("--searches", type=int, default=100)
    parser.add_argument("--baseline", default="config/performance_baseline.json")
    parser.add_argument("--output", default="data/test-reports/performance.json")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    if args.messages < 1 or args.searches < 1:
        parser.error("messages and searches must be positive")

    result = benchmark(messages=args.messages, searches=args.searches)
    baseline = json.loads((PROJECT_ROOT / args.baseline).read_text(encoding="utf-8"))
    checks = {
        "ingest_throughput": result["ingest_messages_per_second"]
        >= baseline["minimum_ingest_messages_per_second"],
        "search_p95": result["search_p95_ms"] <= baseline["maximum_search_p95_ms"],
    }
    report = {"schema_version": 1, "result": result, "baseline": baseline, "checks": checks}
    output = PROJECT_ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.enforce and not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
