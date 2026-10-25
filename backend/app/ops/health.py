"""Operational health: a fixed list of checks, each with a plain verdict, wired to alerts.

A failing or degraded check raises (or refreshes) an alert; a passing check resolves it. The worker runs this hourly. Whether the
*worker itself* is alive is judged from its heartbeat when the dashboard is read, because a dead worker cannot report on itself.
"""

from __future__ import annotations

import os
import shutil
import socket
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.enums import ApprovalStatus, TaskStatus
from app.models.approvals import ApprovalRequest
from app.models.ops import Schedule, WorkerHeartbeat
from app.models.tasks import Task
from app.models.youtube import YouTubeConnection
from app.ops import alerts, schedule
from app.services import audit, backup
from app.services import settings as settings_svc
from app.storage import data_root
from app.timeutil import iso, utcnow
from app.youtube import quota
from app.youtube.crypto import TokenBox

HEARTBEAT_STALE = timedelta(seconds=120)


def worker_status(db: Session) -> dict[str, Any]:
    beats = list(db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_beat.desc())))
    now = utcnow()
    live = [b for b in beats if now - b.last_beat <= HEARTBEAT_STALE]
    return {"alive": bool(live), "workers": [{"worker_id": b.worker_id, "host": b.hostname, "pid": b.pid, "started_at": iso(b.started_at), "last_beat": iso(b.last_beat),
                                              "seconds_since_beat": int((now - b.last_beat).total_seconds()), "current_task_id": b.current_task_id,
                                              "alive": now - b.last_beat <= HEARTBEAT_STALE} for b in beats[:10]],
            "note": "A worker is considered stopped if it has not beaten for two minutes. Scheduled work only happens while one is running."}


def beat(db: Session, worker_id: str, current_task_id: int | None = None, *, started: bool = False) -> None:
    row = db.get(WorkerHeartbeat, worker_id)
    now = utcnow()
    if row is None:
        db.add(WorkerHeartbeat(worker_id=worker_id, hostname=socket.gethostname(), pid=os.getpid(), started_at=now, last_beat=now, current_task_id=current_task_id))
    else:
        if started:
            row.started_at, row.pid = now, os.getpid()
        row.last_beat, row.current_task_id = now, current_task_id


def _c(cid: str, name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"id": cid, "name": name, "status": status, "detail": detail, **extra}  # status: ok | warn | fail


def run_checks(db: Session) -> list[dict[str, Any]]:
    now = utcnow()
    out: list[dict[str, Any]] = []

    try:
        db.execute(text("SELECT 1"))
        quick = db.execute(text("PRAGMA quick_check")).scalar() if db.bind.dialect.name == "sqlite" else "ok"  # type: ignore[union-attr]
        out.append(_c("database", "Database", "ok" if quick == "ok" else "fail", "readable and consistent" if quick == "ok" else f"integrity check says: {quick}"))
    except Exception as e:  # noqa: BLE001
        out.append(_c("database", "Database", "fail", f"cannot read the database: {type(e).__name__}"))

    chain = audit.verify_chain(db)
    out.append(_c("audit", "Audit log", "ok" if chain["ok"] else "fail", f"hash chain intact over {chain.get('checked', '?')} events" if chain["ok"]
                  else f"the audit log fails verification: {chain.get('error') or chain}"))

    free = shutil.disk_usage(data_root()).free / 1024**3
    out.append(_c("disk", "Disk space", "fail" if free < 1 else "warn" if free < 5 else "ok", f"{free:.1f} GiB free where the data lives"))
    out.append(_c("ffmpeg", "FFmpeg", "ok" if shutil.which("ffmpeg") else "warn", "found" if shutil.which("ffmpeg") else "not installed: video rendering is unavailable"))

    w = worker_status(db)
    out.append(_c("worker", "Worker", "ok" if w["alive"] else "warn", "running" if w["alive"] else "no worker has reported in the last two minutes; scheduled work is not running"))

    stuck = db.scalars(select(Task).where(Task.status == TaskStatus.RUNNING.value, Task.lease_expires_at < now - timedelta(minutes=10))).all()
    out.append(_c("stuck", "Stuck tasks", "warn" if stuck else "ok", f"{len(stuck)} running task(s) have an expired lease" if stuck else "none"))

    blocked = db.scalars(select(Task).where(Task.status == TaskStatus.BLOCKED.value)).all()
    old = [t for t in blocked if now - t.updated_at > timedelta(hours=24)]
    unsafe = [t for t in blocked if (t.output_refs or {}).get("external_action_started")]
    out.append(_c("blocked", "Blocked tasks", "warn" if old or unsafe else "ok",
                  (f"{len(blocked)} blocked; {len(old)} for over a day; {len(unsafe)} need you to reconcile an external action" if blocked else "none")))

    failed = db.scalar(select(func.count()).select_from(Task).where(Task.status == TaskStatus.FAILED.value, Task.updated_at > now - timedelta(hours=24))) or 0
    out.append(_c("failed", "Failed tasks (24 h)", "warn" if failed >= 3 else "ok", f"{failed} failed in the last 24 hours"))

    backups = backup.list_backups()
    newest = max((Path(b["path"]).stat().st_mtime for b in backups), default=None)
    age_h = (now.timestamp() - newest) / 3600 if newest else None
    out.append(_c("backup", "Backups", "ok" if age_h is not None and age_h < 36 else "warn",
                  f"latest backup {age_h:.0f} h ago" if age_h is not None else "no backup exists yet (the daily schedule makes one)"))

    pending = db.scalars(select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING.value, ApprovalRequest.created_at < now - timedelta(hours=48))).all()
    out.append(_c("approvals", "Approvals waiting", "warn" if pending else "ok", f"{len(pending)} approval(s) have waited more than 48 hours for you" if pending else "none overdue"))

    overdue = []
    tz = schedule.zone(db)
    for sc in db.scalars(select(Schedule).where(Schedule.enabled.is_(True))):
        missed = schedule.slots_between(sc.cadence, tz, sc.created_at, sc.last_slot or sc.created_at, now)
        if missed and now - missed[0] > timedelta(hours=3):  # the oldest slot nobody has run for three hours
            overdue.append(sc.slug)
    out.append(_c("schedules", "Schedules", "warn" if overdue else "ok", f"overdue and not firing: {', '.join(overdue)}" if overdue else "on time"))

    conns = db.scalars(select(YouTubeConnection)).all()
    bad = [c for c in conns if c.status == "NEEDS_RECONNECT"]
    waiting = [c for c in conns if c.status == "PENDING_CONFIRMATION" and now - c.updated_at > timedelta(days=1)]
    out.append(_c("youtube_connections", "YouTube connections", "warn" if bad else "ok",
                  (f"{len(bad)} channel(s) need to be reconnected" if bad else (f"{len(waiting)} channel(s) signed in but never confirmed" if waiting else "fine")), channels=[c.channel_id for c in bad]))
    q = quota.summary(db)
    out.append(_c("youtube_quota", "YouTube quota", "warn" if q["used_today"] >= 0.8 * max(1, q["daily_limit"]) else "ok", f"{q['used_today']} of {q['daily_limit']} units used today (this app's count)"))
    if any(c.refresh_token_enc for c in conns):
        out.append(_c("token_key", "Token key", "ok" if TokenBox().key_path.is_file() else "fail",
                      "present" if TokenBox().key_path.is_file() else f"{TokenBox().key_path} is missing: stored YouTube permissions cannot be read; reconnect each channel"))

    mode = settings_svc.operating_mode(db).value
    paused = settings_svc.is_emergency_paused(db)
    out.append(_c("control", "Operating state", "warn" if paused else "ok", "EMERGENCY PAUSE is active" if paused else f"mode {mode}"))
    return out


SEVERITY = {"warn": "warning", "fail": "critical"}


def apply(db: Session, checks: list[dict[str, Any]]) -> dict[str, int]:
    """Raise an alert for every unhealthy check and resolve the alerts of healthy ones."""
    raised = resolved = 0
    for c in checks:
        key = f"health:{c['id']}"
        if c["status"] == "ok":
            resolved += bool(alerts.resolve(db, key, "check passes again"))
        else:
            alerts.raise_alert(db, key, SEVERITY[c["status"]], c["name"], c["detail"], source="health")
            raised += 1
    return {"raised": raised, "resolved": resolved}
