"""The persistent scheduler: slots, catch-up, idempotency, restarts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db import dispose_engines, session_scope
from app.enums import TaskStatus as S
from app.models.audit import AuditEvent
from app.models.ops import Schedule
from app.models.tasks import Task
from app.ops import schedule
from app.ops.schedule import ScheduleError
from app.services import settings as settings_svc
from app.worker.runner import HANDLERS, Runner

NPT = ZoneInfo("Asia/Kathmandu")  # UTC+05:45, no daylight saving


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


def make(db, slug="daily", cadence=None, created=None, catch_up="latest", enabled=True, task_type="system.noop"):
    s = Schedule(slug=slug, title=slug.title(), cadence=schedule.validate_cadence(cadence or {"kind": "daily", "at": "08:30"}), task_type=task_type,
                 catch_up=catch_up, enabled=enabled, created_at=created or utc(2026, 11, 1, 0, 0))
    db.add(s)
    db.flush()
    return s


def tasks_for(db, slug):
    return list(db.scalars(select(Task).where(Task.idempotency_key.like(f"sched:{slug}:%")).order_by(Task.id)))


# --- the arithmetic ------------------------------------------------------------------------------------------------------------


def test_daily_slots_are_read_in_the_owners_time_zone():
    got = schedule.slots_between({"kind": "daily", "at": "08:30"}, NPT, utc(2026, 11, 1), utc(2026, 11, 1, 0), utc(2026, 11, 4, 0))
    assert got == [utc(2026, 11, 1, 2, 45), utc(2026, 11, 2, 2, 45), utc(2026, 11, 3, 2, 45)]  # 08:30 in Kathmandu is 02:45 UTC


def test_the_same_clock_time_means_different_instants_in_different_zones():
    la = ZoneInfo("America/Los_Angeles")
    a = schedule.slots_between({"kind": "daily", "at": "08:30"}, la, utc(2026, 10, 30), utc(2026, 10, 30, 12), utc(2026, 11, 2))
    assert a == [utc(2026, 10, 30, 15, 30), utc(2026, 10, 31, 15, 30), utc(2026, 11, 1, 16, 30)]  # the clocks went back on 1 November: the same 08:30 moves an hour in UTC


def test_weekly_and_interval_slots():
    weekly = schedule.slots_between({"kind": "weekly", "days": [0, 3], "at": "09:00"}, NPT, utc(2026, 11, 1), utc(2026, 11, 1), utc(2026, 11, 15))
    assert [s.astimezone(NPT).strftime("%a %d") for s in weekly] == ["Mon 02", "Thu 05", "Mon 09", "Thu 12"]
    every = schedule.slots_between({"kind": "interval", "every_minutes": 60}, NPT, utc(2026, 11, 1, 0, 20), utc(2026, 11, 1, 5), utc(2026, 11, 1, 7, 30))
    assert every == [utc(2026, 11, 1, 5, 20), utc(2026, 11, 1, 6, 20), utc(2026, 11, 1, 7, 20)]  # aligned to when the schedule was made


@pytest.mark.parametrize("bad", [{"kind": "hourly"}, {"kind": "interval", "every_minutes": 1}, {"kind": "interval", "every_minutes": True}, {"kind": "daily", "at": "8.30"},
                                 {"kind": "daily"}, {"kind": "weekly", "days": [], "at": "08:30"}, {"kind": "weekly", "days": [7], "at": "08:30"}])
def test_bad_cadences_are_refused(bad):
    with pytest.raises(ScheduleError):
        schedule.validate_cadence(bad)


# --- ticking -------------------------------------------------------------------------------------------------------------------


def test_a_due_slot_creates_exactly_one_task_however_often_it_is_ticked(db, owner):
    make(db, created=utc(2026, 11, 1, 0, 0))
    now = utc(2026, 11, 2, 3, 0)  # 08:45 in Kathmandu: the 08:30 slot is due
    assert schedule.tick(db, now) == ["daily"] and schedule.tick(db, now) == [] and schedule.tick(db, now + timedelta(minutes=5)) == []
    ts = tasks_for(db, "daily")
    assert len(ts) == 1 and ts[0].input_refs["schedule"] == "daily" and ts[0].input_refs["slot"].startswith("2026-11-02T02:45")
    assert "08:30" in ts[0].title
    assert schedule.tick(db, utc(2026, 11, 3, 3, 0)) == ["daily"] and len(tasks_for(db, "daily")) == 2  # tomorrow's slot, one more task


def test_two_workers_ticking_at_once_cannot_double_up_because_the_slot_is_in_the_task_key(db):
    s = make(db)
    now = utc(2026, 11, 2, 3, 0)
    schedule._enqueue(db, s, utc(2026, 11, 2, 2, 45))  # another worker got there first (and then crashed before recording last_slot)
    db.flush()
    schedule.tick(db, now)
    assert len(tasks_for(db, "daily")) == 1


def test_a_schedule_does_not_owe_slots_from_before_it_existed(db):
    make(db, created=utc(2026, 11, 2, 4, 0))  # made at 09:45 Kathmandu time
    assert schedule.tick(db, utc(2026, 11, 2, 5, 0)) == []  # today's 08:30 had already passed when it was made
    assert schedule.tick(db, utc(2026, 11, 3, 3, 0)) == ["daily"]


def test_after_downtime_the_latest_missed_slot_runs_once_not_every_missed_slot(db):
    make(db, created=utc(2026, 11, 1, 0, 0))
    assert schedule.tick(db, utc(2026, 11, 2, 3, 0)) == ["daily"]  # day 1 ran
    assert schedule.tick(db, utc(2026, 11, 9, 3, 0)) == ["daily"]  # a week of downtime
    assert len(tasks_for(db, "daily")) == 2  # not 8
    assert schedule.tick(db, utc(2026, 11, 9, 4, 0)) == []


def test_skip_policy_runs_only_slots_that_are_just_overdue(db):
    make(db, catch_up="skip", created=utc(2026, 11, 1, 0, 0))
    assert schedule.tick(db, utc(2026, 11, 2, 6, 0)) == []  # three hours late: skipped, and not revisited
    assert schedule.tick(db, utc(2026, 11, 2, 6, 5)) == []
    assert schedule.tick(db, utc(2026, 11, 3, 2, 50)) == ["daily"]  # five minutes after the next slot: within grace


def test_disabled_schedules_do_nothing_and_re_enabling_does_not_owe_missed_slots(db, owner):
    make(db, enabled=False)
    assert schedule.tick(db, utc(2026, 11, 5, 3, 0)) == []
    schedule.set_enabled(db, owner, "daily", True, now=utc(2026, 11, 5, 3, 0))
    assert schedule.tick(db, utc(2026, 11, 5, 3, 0)) == []  # the days it was off are not owed
    assert schedule.tick(db, utc(2026, 11, 6, 3, 0)) == ["daily"]


def test_changing_the_clock_time_does_not_fire_retroactively(db, owner):
    make(db)
    schedule.set_cadence(db, owner, "daily", {"kind": "daily", "at": "09:00"}, now=utc(2026, 11, 2, 0, 0))
    assert schedule.tick(db, utc(2026, 11, 2, 1, 0)) == []
    assert schedule.tick(db, utc(2026, 11, 2, 3, 30)) == ["daily"] and tasks_for(db, "daily")[0].input_refs["slot"].startswith("2026-11-02T03:15")


def test_the_time_zone_is_the_owners_setting_and_must_be_real(db, owner):
    make(db)
    settings_svc.set_value(db, owner, "timezone", "Asia/Tokyo")
    assert schedule.tick(db, utc(2026, 11, 2, 0, 0)) == ["daily"]  # 08:30 in Tokyo is 23:30 UTC the day before
    with pytest.raises(settings_svc.SettingError):
        settings_svc.set_value(db, owner, "timezone", "Mars/Olympus")


def test_only_the_owner_controls_schedules(db, agent, owner):
    make(db)
    for fn in (lambda: schedule.set_enabled(db, agent("ceo"), "daily", False), lambda: schedule.run_now(db, agent("coo"), "daily"),
               lambda: schedule.set_cadence(db, agent("ceo"), "daily", {"kind": "daily", "at": "10:00"})):
        with pytest.raises(ScheduleError, match="owner"):
            fn()
    tid = schedule.run_now(db, owner, "daily")
    assert db.get(Task, tid).input_refs["schedule"] == "daily"
    assert "schedule.run_now" in [e.action for e in db.scalars(select(AuditEvent))]
    with pytest.raises(ScheduleError):
        schedule.get(db, "nope")


def test_default_schedules_are_seeded_once_and_never_overwrite_the_owner(db, owner):
    assert set(schedule.seed_defaults(db)) == {"health-check", "daily-cycle", "backup", "analytics-collect", "comments-collect"}
    schedule.set_cadence(db, owner, "daily-cycle", {"kind": "daily", "at": "06:00"})
    assert schedule.seed_defaults(db) == []
    assert schedule.get(db, "daily-cycle").cadence == {"kind": "daily", "at": "06:00"}


def test_the_view_says_what_is_due_and_when_the_next_run_is(db):
    s = make(db, created=utc(2020, 1, 1))
    v = schedule.serialize(db, s)
    assert v["timezone"] == "Asia/Kathmandu" and v["next_run"] and v["enabled"]
    s.last_slot = utc(2999, 1, 1)
    assert schedule.serialize(db, s)["due_now"] is False


# --- surviving a restart ---------------------------------------------------------------------------------------------------------


def test_schedules_and_their_progress_survive_a_restart(db, env):
    from app.config import get_settings
    from app.db import configure_engine, session_scope

    make(db, created=utc(2026, 11, 1, 0, 0))
    db.commit()
    assert Runner("w1", HANDLERS).housekeeping(now=utc(2026, 11, 2, 3, 0)) == ["daily"]
    dispose_engines()  # the whole process "restarts": new engine, new connections
    configure_engine(get_settings().db_url)
    with session_scope(write=False) as s2:
        sc = s2.scalar(select(Schedule))
        assert sc.slug == "daily" and sc.last_slot == utc(2026, 11, 2, 2, 45)
    assert Runner("w2", HANDLERS).housekeeping(now=utc(2026, 11, 2, 3, 5)) == []  # the new process does not repeat the slot
    assert Runner("w2", HANDLERS).housekeeping(now=utc(2026, 11, 3, 3, 0)) == ["daily"]
    with session_scope(write=False) as s2:
        assert len(tasks_for(s2, "daily")) == 2


def test_scheduled_tasks_run_to_completion_through_the_real_queue(db):
    make(db, task_type="system.noop")
    db.commit()
    Runner("w", HANDLERS).housekeeping(now=utc(2026, 11, 2, 3, 0))
    assert Runner("w", HANDLERS).run_once() is not None
    with session_scope(write=False) as s:
        t = tasks_for(s, "daily")[0]
        assert t.status == S.COMPLETED.value and t.review_required is False
