"""Diagnostic views (Layers A and B): operational detail and CEO verification. Owner only, and audited."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import envcheck
from app.api.deps import Auth, require
from app.db import get_wdb
from app.models.audit import AuditEvent
from app.models.board import BoardEntry
from app.models.reports import Report
from app.models.tasks import Evidence, Task
from app.security.permissions import P
from app.services import audit, backup, board, evidence, tasks
from app.services import reports as reports_svc
from app.timeutil import iso

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


def _viewed(db: Session, auth: Auth, request: Request, **details: Any) -> None:
    audit.record(db, auth.actor, "diagnostics.viewed", "diagnostics", request.url.path, details)


@router.get("/tasks")
def list_tasks(request: Request, status: str | None = None, limit: int = 100, db: Session = Depends(get_wdb, scope="function"),
               auth: Auth = Depends(require(P.DIAG_READ))):
    q = select(Task).order_by(Task.id.desc()).limit(min(limit, 500))
    if status:
        q = q.where(Task.status == status)
    _viewed(db, auth, request, status=status)
    return [tasks.detail(db, t) for t in db.scalars(q)]


@router.get("/tasks/{task_id}")
def get_task(task_id: int, request: Request, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.DIAG_READ))):
    t = db.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Task not found.")
    _viewed(db, auth, request)
    return tasks.detail(db, t)


@router.get("/reports")
def list_reports(request: Request, kind: str | None = None, limit: int = 50, db: Session = Depends(get_wdb, scope="function"),
                 auth: Auth = Depends(require(P.DIAG_READ))):
    q = select(Report).order_by(Report.id.desc()).limit(min(limit, 200))
    if kind:
        q = q.where(Report.kind == kind)
    _viewed(db, auth, request, kind=kind)
    return [{"id": r.id, "kind": r.kind, "title": r.title, "author": r.author, "status": r.status,
             "created_at": iso(r.created_at), "summary": r.summary_text} for r in db.scalars(q)]


@router.get("/reports/{report_id}")
def get_report(report_id: int, request: Request, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.DIAG_READ))):
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Report not found.")
    _viewed(db, auth, request)
    return reports_svc.serialize(r, include_verification=True)


@router.get("/board")
def full_board(request: Request, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.DIAG_READ))):
    _viewed(db, auth, request)
    return board.poster(db, owner_view=False)


@router.get("/board/{public_id}")
def board_entry(public_id: str, request: Request, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.DIAG_READ))):
    e = db.scalar(select(BoardEntry).where(BoardEntry.public_id == public_id))
    if e is None:
        raise HTTPException(404, "Entry not found.")
    _viewed(db, auth, request)
    return {"entry": board.serialize(db, e), "history": board.entry_history(db, e.id)}


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: int, request: Request, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.DIAG_READ))):
    ev = db.get(Evidence, evidence_id)
    if ev is None:
        raise HTTPException(404, "Evidence not found.")
    _viewed(db, auth, request)
    return evidence.serialize(ev)


@router.get("/audit")
def audit_log(request: Request, action: str | None = None, limit: int = 200, db: Session = Depends(get_wdb, scope="function"),
              auth: Auth = Depends(require(P.DIAG_READ))):
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 1000))
    if action:
        q = q.where(AuditEvent.action == action)
    rows = [{"id": e.id, "ts": iso(e.ts), "actor": f"{e.actor_kind}:{e.actor_id}", "action": e.action,
             "entity": f"{e.entity_type}:{e.entity_id}" if e.entity_type else None, "details": e.details,
             "hash": e.hash[:16]} for e in db.scalars(q)]
    _viewed(db, auth, request, action=action)
    return rows


@router.get("/audit/verify")
def verify_audit(request: Request, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.DIAG_READ))):
    _viewed(db, auth, request)
    return audit.verify_chain(db)


@router.get("/environment")
def environment(request: Request, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.DIAG_READ))):
    _viewed(db, auth, request)
    return {"environment": envcheck.inspect(), "backups": backup.list_backups()}


@router.post("/backup")
def make_backup(request: Request, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.SYSTEM_MAINTENANCE))):
    try:
        info = backup.create_backup("manual")
    except backup.BackupError as e:
        raise HTTPException(500, str(e)) from e
    audit.record(db, auth.actor, "backup.created", "backup", info["path"], {"bytes": info["bytes"]})
    return {k: info[k] for k in ("path", "bytes", "integrity", "created_at")}
