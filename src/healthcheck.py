"""Container health-check entry point."""

from __future__ import annotations

import argparse
import json

from src.core.config import load_config
from src.core.health import check_health


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--state", default="data/health.json")
    parser.add_argument("--database", default="data/messages.db")
    parser.add_argument("--max-age", type=float, default=90.0)
    parser.add_argument("--require-napcat", action="store_true")
    args = parser.parse_args()

    try:
        load_config(config_dir=args.config_dir)
        report = check_health(
            args.state,
            database=args.database,
            max_age=args.max_age,
            require_napcat=args.require_napcat,
        )
    except Exception as exc:
        report = {"healthy": False, "errors": [f"configuration invalid: {exc}"]}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if report["healthy"] else 1)


if __name__ == "__main__":
    main()
