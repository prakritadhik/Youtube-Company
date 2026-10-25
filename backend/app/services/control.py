"""Emergency pause, resume, and the checks other services use before doing anything external."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import OperatingMode, TaskStatus
from app.models.tasks import Task
from app.security.permissions import Actor, P
from app.services import audit
from app.services import settings as settings_svc
from app.timeutil import iso, utcnow


class ExternalActionBlocked(Exception):
    """An external or paid action is not currently permitted."""

    def __init__(self, reason: str, code: str):
        super().__init__(reason)
        self.reason = reason
        self.code = code


# Actions that touch the outside world, by kind.
EXTERNAL_WRITE_KINDS = {"publish", "reply", "upload"}
PAID_KINDS = {"paid_generation"}


def pause_report(db: Session) -> dict[str, Any]:
    """What an emergency pause does and does not stop right now, from actual task records."""
    counts = dict(
        db.execute(select(Task.status, func.count()).group_by(Task.status)).all()
    )
    running = db.scalars(select(Task).where(Task.status == TaskStatus.RUNNING.value)).all()
    external_running = [t for t in running if t.task_type.startswith(("youtube.", "provider."))]
    return {
        "new_tasks_blocked": True,
        "new_paid_generation_blocked": True,
        "new_uploads_blocked": True,
        "automated_replies_blocked": True,
        "queued_tasks_held": counts.get(TaskStatus.QUEUED.value, 0)
        + counts.get(TaskStatus.RETRY_SCHEDULED.value, 0),
        "running_tasks_not_interrupted": len(running),
        "running_task_ids": [t.id for t in running],
        "external_actions_in_flight": [
            {"task_id": t.id, "type": t.task_type, "note": "already started; cannot be recalled"}
            for t in external_running
        ],
        "limits": (
            "Tasks already running finish their current step and then stop at the next pause check. "
            "An upload or API request that has already been sent to a provider cannot be cancelled."
        ),
    }


def pause(db: Session, actor: Actor, reason: str) -> dict[str, Any]:
    actor.require(P.CONTROL_PAUSE)
    if not reason.strip():
        raise settings_svc.SettingError("a reason is required to pause the company")
    state = {"active": True, "since": iso(utcnow()), "by": actor.label, "reason": reason.strip()}
    settings_svc._write_internal(db, actor, "emergency_pause", state)
    report = pause_report(db)
    audit.record(db, actor, "control.paused", "setting", "emergency_pause", {"reason": reason, "report": report})
    return report


def resume(db: Session, actor: Actor, note: str = "") -> None:
    actor.require(P.CONTROL_RESUME)
    if not actor.is_owner:
        raise settings_svc.PermissionDenied(actor, P.CONTROL_RESUME)
    state = {"active": False, "since": None, "by": None, "reason": None}
    settings_svc._write_internal(db, actor, "emergency_pause", state)
    audit.record(db, actor, "control.resumed", "setting", "emergency_pause", {"note": note})


def assert_can_start_work(db: Session) -> None:
    """New production tasks must not start while the company is paused."""
    if settings_svc.is_emergency_paused(db):
        raise ExternalActionBlocked("Emergency pause is active.", "EMERGENCY_PAUSE")


def assert_external_action_allowed(db: Session, kind: str) -> None:
    """Gate for publishing, replies, uploads and paid generation (budget is checked separately)."""
    if settings_svc.is_emergency_paused(db):
        raise ExternalActionBlocked("Emergency pause is active.", "EMERGENCY_PAUSE")
    mode = settings_svc.operating_mode(db)
    if mode is OperatingMode.PAUSED:
        raise ExternalActionBlocked("Operating mode is PAUSED.", "MODE_PAUSED")
    if mode is OperatingMode.DEVELOPMENT and kind in EXTERNAL_WRITE_KINDS:
        raise ExternalActionBlocked(
            "DEVELOPMENT mode: external publishing and public replies are disabled.", "MODE_DEVELOPMENT"
        )
