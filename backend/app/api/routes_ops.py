"""Operations: worker health, schedules, alerts and the latest health check and daily cycle."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Auth, require
from app.db import get_db, get_wdb
from app.enums import TaskStatus
from app.models.ops import Schedule
from app.models.tasks import Task
from app.ops import alerts, health, schedule
from app.security.permissions import P
from app.services import settings as settings_svc
from app.storage import UnsafePath, resolve_data_path

router = APIRouter(prefix="/api/ops", tags=["operations"])


class CadenceIn(BaseModel):
    cadence: dict[str, Any]


def _latest(db: Session, task_type: str) -> Task | None:
    return db.scalar(select(Task).where(Task.task_type == task_type, Task.status == TaskStatus.COMPLETED.value).order_by(Task.completed_at.desc()))


def _cycle_detail(t: Task | None) -> dict[str, Any] | None:
    if t is None:
        return None
    try:
        return json.loads(resolve_data_path(t.result["report_ref"]).read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError, UnsafePath):
        return {"ran_at": (t.result or {}).get("ran_at"), "phases": [], "note": "the detailed record is no longer on disk"}


@router.get("/summary")
def summary(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    w = health.worker_status(db)
    return {"alerts": alerts.counts(db), "worker_alive": w["alive"]}


@router.get("/status")
def status(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    hc, cy = _latest(db, "ops.health_check"), _latest(db, "ops.daily_cycle")
    return {
        "worker": health.worker_status(db),
        "schedules": [schedule.serialize(db, s) for s in db.scalars(select(Schedule).order_by(Schedule.id))],
        "alerts": [alerts.serialize(a) for a in alerts.list_alerts(db)], "alert_counts": alerts.counts(db),
        "health": {"checked_at": (hc.result or {}).get("checked_at"), "checks": (hc.result or {}).get("checks", [])} if hc else None,
        "daily_cycle": _cycle_detail(cy),
        "timezone": settings_svc.get(db, "timezone"), "operating_mode": settings_svc.operating_mode(db).value,
        "notes": ["Alerts appear here and on every page; no e-mail or phone message is sent because none is configured.",
                  "Scheduled work only runs while the worker is running (python launcher.py starts it)."],
    }


@router.get("/alerts")
def list_alerts(include_resolved: bool = False, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return {"alerts": [alerts.serialize(a) for a in alerts.list_alerts(db, include_resolved=include_resolved)]}


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    try:
        return alerts.serialize(alerts.acknowledge(db, auth.actor, alert_id))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@router.post("/schedules/{slug}/run-now")
def run_now(slug: str, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    return {"task_id": schedule.run_now(db, auth.actor, slug)}


@router.post("/schedules/{slug}/enable")
def enable(slug: str, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    return schedule.serialize(db, schedule.set_enabled(db, auth.actor, slug, True))


@router.post("/schedules/{slug}/disable")
def disable(slug: str, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    return schedule.serialize(db, schedule.set_enabled(db, auth.actor, slug, False))


@router.put("/schedules/{slug}")
def change(slug: str, body: CadenceIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    return schedule.serialize(db, schedule.set_cadence(db, auth.actor, slug, body.cadence))
