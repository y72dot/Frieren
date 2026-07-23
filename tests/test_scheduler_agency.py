"""Tests for SchedulerAgency (PLUG-503)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from src.plugin.context import PermissionDeniedError
from src.plugin.scheduler_agency import SchedulerAgency

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeManifestPermissions:
    scheduler: bool = False


def _make_agency(*, scheduler_perm: bool = True) -> SchedulerAgency:
    from src.core.runtime.scheduler import SchedulerService, ScheduleStore

    conn = sqlite3.connect(":memory:")
    store = ScheduleStore(conn)
    svc = SchedulerService(store, MagicMock())
    perms = FakeManifestPermissions(scheduler=scheduler_perm)
    return SchedulerAgency(svc, "test_plugin", perms, 1)


# ---------------------------------------------------------------------------
# Permission denied
# ---------------------------------------------------------------------------


class TestPermissionDenied:
    async def test_create_schedule_denied_without_permission(self):
        """create_schedule raises PermissionDeniedError when scheduler=False."""
        agency = _make_agency(scheduler_perm=False)
        with pytest.raises(PermissionDeniedError):
            await agency.create_schedule(
                "test",
                "once",
                {"at": int(time.time()) + 3600},
                {"goal": "test"},
            )

    async def test_list_schedules_denied_without_permission(self):
        """list_schedules raises PermissionDeniedError when scheduler=False."""
        agency = _make_agency(scheduler_perm=False)
        with pytest.raises(PermissionDeniedError):
            await agency.list_schedules()

    async def test_cancel_schedule_denied_without_permission(self):
        """cancel_schedule raises PermissionDeniedError when scheduler=False."""
        agency = _make_agency(scheduler_perm=False)
        with pytest.raises(PermissionDeniedError):
            await agency.cancel_schedule("any-id")


# ---------------------------------------------------------------------------
# Schedule CRUD
# ---------------------------------------------------------------------------


class TestScheduleCRUD:
    async def test_create_schedule_returns_schedule_id(self):
        """create_schedule returns a non-empty schedule_id."""
        agency = _make_agency()
        sid = await agency.create_schedule(
            "test_once",
            "once",
            {"at": int(time.time()) + 3600},
            {"goal": "test"},
        )
        assert sid
        assert isinstance(sid, str)

    async def test_list_schedules_returns_created(self):
        """list_schedules returns the schedule that was just created."""
        agency = _make_agency()
        await agency.create_schedule(
            "my_schedule",
            "once",
            {"at": int(time.time()) + 3600},
            {"goal": "test"},
        )
        schedules = await agency.list_schedules()
        assert len(schedules) == 1
        assert schedules[0]["name"] == "my_schedule"

    async def test_list_schedules_returns_only_plugin_owned(self):
        """list_schedules only returns schedules for the owning plugin."""
        agency1 = _make_agency()
        agency2 = _make_agency()

        # Modify agency2's plugin_id
        agency2._plugin_id = "other_plugin"

        await agency1.create_schedule(
            "s1", "once", {"at": int(time.time()) + 3600}, {"goal": "a"}
        )
        await agency2.create_schedule(
            "s2", "once", {"at": int(time.time()) + 3600}, {"goal": "b"}
        )

        scheds1 = await agency1.list_schedules()
        scheds2 = await agency2.list_schedules()
        assert len(scheds1) == 1
        assert scheds1[0]["name"] == "s1"
        assert len(scheds2) == 1
        assert scheds2[0]["name"] == "s2"

    async def test_cancel_schedule_removes_it(self):
        """cancel_schedule removes the schedule from the list."""
        agency = _make_agency()
        sid = await agency.create_schedule(
            "temp",
            "once",
            {"at": int(time.time()) + 3600},
            {"goal": "test"},
        )
        assert len(await agency.list_schedules()) == 1
        result = await agency.cancel_schedule(sid)
        assert result is True
        assert len(await agency.list_schedules()) == 0

    async def test_cancel_schedule_returns_false_for_other_plugin(self):
        """cancel_schedule returns False for a schedule owned by another plugin."""
        agency1 = _make_agency()
        agency2 = _make_agency()
        agency2._plugin_id = "other_plugin"

        sid = await agency2.create_schedule(
            "other_sched",
            "once",
            {"at": int(time.time()) + 3600},
            {"goal": "test"},
        )
        result = await agency1.cancel_schedule(sid)
        assert result is False

    async def test_pause_resume_schedule(self):
        """pause_schedule disables; resume_schedule re-enables."""
        agency = _make_agency()
        sid = await agency.create_schedule(
            "pausable",
            "once",
            {"at": int(time.time()) + 3600},
            {"goal": "test"},
        )
        assert await agency.pause_schedule(sid) is True
        scheds = await agency.list_schedules()
        assert scheds[0]["enabled"] is False

        assert await agency.resume_schedule(sid) is True
        scheds = await agency.list_schedules()
        assert scheds[0]["enabled"] is True
