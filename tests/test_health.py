from __future__ import annotations

import json
import sqlite3

from src.core.health import HealthMonitor, check_health


def test_health_report_checks_heartbeat_database_and_napcat(tmp_path):
    state = tmp_path / "health.json"
    database = tmp_path / "messages.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE probe (id INTEGER)")
    connection.commit()
    connection.close()
    monitor = HealthMonitor(state)
    monitor.write("running", napcat_connected=True, details={"phase": "test"})
    timestamp = json.loads(state.read_text(encoding="utf-8"))["timestamp"]

    report = check_health(
        state,
        database=database,
        max_age=30,
        require_napcat=True,
        now=timestamp + 1,
    )

    assert report["healthy"] is True
    assert report["database"] == "ok"
    assert report["napcat_connected"] is True


def test_health_report_rejects_stale_stopped_or_disconnected_state(tmp_path):
    state = tmp_path / "health.json"
    monitor = HealthMonitor(state)
    monitor.write("stopped", napcat_connected=False)
    timestamp = json.loads(state.read_text(encoding="utf-8"))["timestamp"]

    report = check_health(
        state, max_age=10, require_napcat=True, now=timestamp + 20
    )

    assert report["healthy"] is False
    assert len(report["errors"]) == 3


def test_health_report_rejects_missing_heartbeat(tmp_path):
    report = check_health(tmp_path / "missing.json")
    assert report["healthy"] is False
    assert "heartbeat unreadable" in report["errors"][0]
