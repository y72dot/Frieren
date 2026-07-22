from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


class HealthMonitor:
    """Atomic process heartbeat consumed by the container health check."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("BOT_HEALTH_FILE", "data/health.json"))

    def write(
        self,
        status: str,
        *,
        napcat_connected: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": status,
            "timestamp": time.time(),
            "pid": os.getpid(),
            "napcat_connected": napcat_connected,
            "details": details or {},
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, self.path)


def check_health(
    state_path: str | Path,
    *,
    database: str | Path | None = None,
    max_age: float = 90.0,
    require_napcat: bool = False,
    max_consecutive_event_errors: int = 3,
    now: float | None = None,
) -> dict[str, Any]:
    """Return a machine-readable health report without mutating runtime state."""

    errors: list[str] = []
    path = Path(state_path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"healthy": False, "errors": [f"heartbeat unreadable: {exc}"]}

    age = (time.time() if now is None else now) - float(state.get("timestamp", 0))
    if state.get("status") != "running":
        errors.append(f"process status is {state.get('status')!r}")
    if age < 0 or age > max_age:
        errors.append(f"heartbeat age {age:.1f}s exceeds {max_age:.1f}s")
    if require_napcat and not state.get("napcat_connected"):
        errors.append("NapCat is not connected")
    details = state.get("details")
    if not isinstance(details, dict):
        details = {}
    consecutive_event_errors = int(details.get("consecutive_event_errors", 0) or 0)
    if consecutive_event_errors >= max_consecutive_event_errors:
        errors.append(
            "event dispatch failed "
            f"{consecutive_event_errors} consecutive times: "
            f"{details.get('last_event_error', 'unknown error')}"
        )

    database_status = "not_checked"
    if database is not None:
        try:
            connection = sqlite3.connect(f"file:{Path(database)}?mode=rw", uri=True)
            result = connection.execute("PRAGMA quick_check").fetchone()
            connection.close()
            database_status = str(result[0]) if result else "no_result"
            if database_status != "ok":
                errors.append(f"database quick_check: {database_status}")
        except sqlite3.Error as exc:
            database_status = "error"
            errors.append(f"database unavailable: {exc}")

    return {
        "healthy": not errors,
        "errors": errors,
        "heartbeat_age_seconds": round(age, 3),
        "napcat_connected": bool(state.get("napcat_connected")),
        "database": database_status,
        "pid": state.get("pid"),
        "consecutive_event_errors": consecutive_event_errors,
    }
