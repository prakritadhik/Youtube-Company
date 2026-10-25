"""The persistent scheduler: recurring jobs that keep running when the browser and any AI chat are closed.

How it stays correct:

* Schedules live in the database, so they survive restarts.
* Time is read in the owner's time zone (``timezone`` setting). A "slot" is one occurrence ("08:30 on 2026-11-03"). Each slot
  creates at most one task: the slot is part of the task's idempotency key, so two workers, a crash between "created" and
  "recorded", or a restart can never run a slot twice.
* If the app was off when slots came due, the schedule's ``catch_up`` policy decides: ``latest`` runs the most recent missed slot
  once (a week offline gives one run, not seven); ``skip`` runs nothing unless the slot is only just overdue.
* A schedule never runs on days that predate it (first slot is after its creation).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ops import Schedule
from app.security.permissions import Actor
from app.services import audit, tasks
from app.services import settings as settings_svc
from app.timeutil import iso, utcnow

GRACE = timedelta(minutes=10)  # how overdue a slot may be under catch_up="skip" and still run
MAX_SLOTS_SCANNED = 5000


class ScheduleError(ValueError):
    pass


def _parse_time(s: str) -> time:
    try:
        h, m = (int(x) for x in str(s).split(":"))
        return time(h, m)
    except (ValueError, TypeError) as e:
        raise ScheduleError(f"'at' must look like 08:30, not {s!r}") from e


def validate_cadence(c: dict[str, Any]) -> dict[str, Any]:
    kind = c.get("kind")
    if kind == "interval":
        n = c.get("every_minutes")
        if isinstance(n, bool) or not isinstance(n, int) or not 5 <= n <= 7 * 24 * 60:
            raise ScheduleError("every_minutes must be a whole number from 5 to 10080")
        return {"kind": "interval", "every_minutes": n}
    if kind == "daily":
        _parse_time(c.get("at", ""))
        return {"kind": "daily", "at": c["at"]}
    if kind == "weekly":
        days = c.get("days")
        if not isinstance(days, list) or not days or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days):
            raise ScheduleError("days must be a list of weekday numbers 0 (Monday) to 6 (Sunday)")
        _parse_time(c.get("at", ""))
        return {"kind": "weekly", "days": sorted(set(days)), "at": c["at"]}
    raise ScheduleError("cadence kind must be interval, daily or weekly")


def zone(db: Session) -> ZoneInfo:
    return ZoneInfo(str(settings_svc.get(db, "timezone")))


def slots_between(cadence: dict[str, Any], tz: ZoneInfo, anchor: datetime, after: datetime, upto: datetime) -> list[datetime]:
    """All slots ``s`` with ``after < s <= upto`` (UTC). ``anchor`` fixes interval alignment."""
    if upto <= after:
        return []
    out: list[datetime] = []
    kind = cadence["kind"]
    if kind == "interval":
        step = timedelta(minutes=cadence["every_minutes"])
        k = max(0, int((after - anchor) / step))
        t = anchor + k * step
        while t <= upto and len(out) < MAX_SLOTS_SCANNED:
            if t > after:
                out.append(t)
            t += step
        return out
    at = _parse_time(cadence["at"])
    day: date = after.astimezone(tz).date() - timedelta(days=1)
    last_day: date = upto.astimezone(tz).date() + timedelta(days=1)
    while day <= last_day and len(out) < MAX_SLOTS_SCANNED:
        if kind == "daily" or day.weekday() in cadence["days"]:
            slot = datetime.combine(day, at, tzinfo=tz).astimezone(UTC)
            if after < slot <= upto:
                out.append(slot)
        day += timedelta(days=1)
    return out


def next_slot(s: Schedule, tz: ZoneInfo, now: datetime | None = None) -> datetime | None:
    """The first slot strictly after now (slots already overdue are reported separately as ``due_now``)."""
    now = now or utcnow()
    found = slots_between(s.cadence, tz, s.created_at, now, now + timedelta(days=370))
    return found[0] if found else None


def due_slot(s: Schedule, tz: ZoneInfo, now: datetime) -> datetime | None:
    """The slot to run now, if any, honouring the catch-up policy."""
    after = s.last_slot or s.created_at
    slots = slots_between(s.cadence, tz, s.created_at, after, now)
    if not slots:
        return None
    latest = slots[-1]
    if s.catch_up == "skip" and now - latest > GRACE:
        return None
    return latest


def _enqueue(db: Session, s: Schedule, slot: datetime, *, manual: bool = False) -> int:
    actor = Actor.system("scheduler")
    key = f"sched:{s.slug}:{slot.strftime('%Y%m%dT%H%M%SZ')}" if not manual else f"sched-manual:{s.slug}:{utcnow().strftime('%Y%m%dT%H%M%S%fZ')}"
    t = tasks.create(db, actor, title=f"{s.title} ({'run now' if manual else slot.astimezone(zone(db)).strftime('%Y-%m-%d %H:%M')})", task_type=s.task_type,
                     assigned_agent_slug=s.assigned_agent_slug, input_refs={**s.input_refs, "schedule": s.slug, "slot": iso(slot)}, idempotency_key=key,
                     review_required=s.assigned_agent_slug is not None)  # a system job has no manager to review it; its evidence is still recorded
    return t.id


def tick(db: Session, now: datetime | None = None) -> list[str]:
    """Create tasks for every schedule with a due slot. Safe to call from any number of workers, as often as you like."""
    now = now or utcnow()
    tz = zone(db)
    fired: list[str] = []
    for s in db.scalars(select(Schedule).where(Schedule.enabled.is_(True)).order_by(Schedule.id)):
        slot = due_slot(s, tz, now)
        after = s.last_slot or s.created_at
        if slot is None:
            # nothing to run, but skipped slots must not be revisited next tick
            skipped = slots_between(s.cadence, tz, s.created_at, after, now)
            if skipped:
                s.last_slot = skipped[-1]
            continue
        s.last_task_id = _enqueue(db, s, slot)
        s.last_slot, s.last_run_at = slot, now
        audit.record(db, Actor.system("scheduler"), "schedule.fired", "schedule", s.id, {"slug": s.slug, "slot": iso(slot), "task": s.last_task_id})
        fired.append(s.slug)
    return fired


def run_now(db: Session, actor: Actor, slug: str) -> int:
    if not actor.is_owner:
        raise ScheduleError("only the owner can run a schedule on demand")
    s = get(db, slug)
    tid = _enqueue(db, s, utcnow(), manual=True)
    s.last_task_id, s.last_run_at = tid, utcnow()
    audit.record(db, actor, "schedule.run_now", "schedule", s.id, {"slug": slug, "task": tid})
    return tid


def get(db: Session, slug: str) -> Schedule:
    s = db.scalar(select(Schedule).where(Schedule.slug == slug))
    if s is None:
        raise ScheduleError(f"unknown schedule '{slug}'")
    return s


def set_enabled(db: Session, actor: Actor, slug: str, enabled: bool, *, now: datetime | None = None) -> Schedule:
    if not actor.is_owner:
        raise ScheduleError("only the owner can enable or disable a schedule")
    s = get(db, slug)
    if enabled and not s.enabled:
        s.last_slot = now or utcnow()  # a schedule that was off does not owe the slots it missed
    s.enabled = enabled
    audit.record(db, actor, "schedule.enabled" if enabled else "schedule.disabled", "schedule", s.id, {"slug": slug})
    return s


def set_cadence(db: Session, actor: Actor, slug: str, cadence: dict[str, Any], *, now: datetime | None = None) -> Schedule:
    if not actor.is_owner:
        raise ScheduleError("only the owner can change a schedule")
    s = get(db, slug)
    s.cadence = validate_cadence(cadence)
    s.last_slot = now or utcnow()
    audit.record(db, actor, "schedule.changed", "schedule", s.id, {"slug": slug, "cadence": s.cadence})
    return s


DEFAULTS: list[dict[str, Any]] = [
    {"slug": "health-check", "title": "Health check", "description": "Checks the database, audit log, disk, worker, backups, connections and quotas, and raises or clears alerts.",
     "cadence": {"kind": "interval", "every_minutes": 60}, "task_type": "ops.health_check", "catch_up": "latest"},
    {"slug": "daily-cycle", "title": "Daily operating cycle", "description": "Reviews the company, re-assesses open ideas, lists what needs you, queues cleared uploads only where autonomy allows, then builds the executive report.",
     "cadence": {"kind": "daily", "at": "07:30"}, "task_type": "ops.daily_cycle", "catch_up": "latest"},
    {"slug": "backup", "title": "Database backup", "description": "A verified SQLite backup. Copy data/ (including data/secrets/) elsewhere for a full backup.",
     "cadence": {"kind": "daily", "at": "02:00"}, "task_type": "system.backup_database", "catch_up": "latest"},
]


def seed_defaults(db: Session) -> list[str]:
    """Create the standard schedules that do not exist yet. Never overwrites an owner's changes. Internal work only: nothing external or paid."""
    made = []
    for d in DEFAULTS:
        if db.scalar(select(Schedule).where(Schedule.slug == d["slug"])) is None:
            db.add(Schedule(slug=d["slug"], title=d["title"], description=d["description"], cadence=validate_cadence(d["cadence"]), task_type=d["task_type"],
                            assigned_agent_slug=d.get("assigned_agent_slug"), input_refs=d.get("input_refs", {}), catch_up=d["catch_up"], enabled=True))
            made.append(d["slug"])
    db.flush()
    return made


def serialize(db: Session, s: Schedule) -> dict[str, Any]:
    tz = zone(db)
    nxt = next_slot(s, tz) if s.enabled else None
    due = bool(s.enabled and due_slot(s, tz, utcnow()))
    return {"due_now": due, "slug": s.slug, "title": s.title, "description": s.description, "cadence": s.cadence, "enabled": s.enabled, "catch_up": s.catch_up,
            "task_type": s.task_type, "last_slot": iso(s.last_slot) if s.last_slot else None, "last_run_at": iso(s.last_run_at) if s.last_run_at else None,
            "last_task_id": s.last_task_id, "next_run": iso(nxt) if nxt else None, "timezone": str(tz)}
