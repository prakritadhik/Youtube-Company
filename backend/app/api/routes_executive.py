"""Owner-facing executive layer (Layer C). Nothing here returns raw worker output, errors or CEO check detail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Auth, require
from app.api.schemas import Decision
from app.db import get_db, get_wdb
from app.enums import ReportKind
from app.models.board import BoardEntry
from app.models.org import Channel
from app.models.reports import Report
from app.models.videos import Video
from app.security.permissions import P
from app.services import approvals, audit, board, finance, overview, reports, videos

router = APIRouter(prefix="/api", tags=["executive"])


@router.get("/executive/overview")
def get_overview(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return overview.executive_overview(db)


def _exec_report(r: Report) -> dict:
    return reports.serialize(r, include_verification=False)


@router.get("/executive/reports")
def list_reports(limit: int = 30, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    rows = db.scalars(
        select(Report).where(Report.kind == ReportKind.EXECUTIVE.value).order_by(Report.id.desc()).limit(min(limit, 100))
    )
    return [{"id": r.id, "created_at": r.created_at.isoformat(), "headline": r.summary_text,
             "period": r.content.get("period"), "check_summary": r.content.get("check_summary")} for r in rows]


@router.get("/executive/reports/latest")
def latest_report(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    r = reports.latest_executive(db)
    if r is None:
        return {"available": False, "reason": "No executive report has been generated yet."}
    return {"available": True, "report": _exec_report(r)}


@router.get("/executive/reports/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    r = db.get(Report, report_id)
    if r is None or r.kind != ReportKind.EXECUTIVE.value:  # department/operations reports are Layer A/B
        raise HTTPException(404, "Report not found.")
    return _exec_report(r)


@router.post("/executive/reports/generate")
def generate_report(db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.SYSTEM_MAINTENANCE))):
    rep = reports.run_reporting_cycle(db)
    audit.record(db, auth.actor, "report.generate_requested", "report", rep.id, {})
    return {"available": True, "report": _exec_report(rep)}


@router.get("/channels")
def list_channels(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return [{"slug": c.slug, "name": c.name, "kind": c.kind, "objective": c.objective}
            for c in db.scalars(select(Channel).order_by(Channel.id))]


@router.get("/channels/{slug}")
def channel(slug: str, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    try:
        return overview.channel_dashboard(db, slug)
    except LookupError as e:
        raise HTTPException(404, "Channel not found.") from e


@router.get("/organization")
def organization(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return overview.organization_view(db)


@router.get("/videos")
def list_videos(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return [videos.serialize(db, v) for v in db.scalars(select(Video).order_by(Video.id.desc()).limit(200))]


@router.get("/rights")
def rights(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return overview.rights_dashboard(db)


@router.get("/costs")
def costs(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return finance.spend_summary(db)


@router.get("/board")
def get_board(db: Session = Depends(get_db), auth: Auth = Depends(require(P.BOARD_READ))):
    return board.poster(db, owner_view=True)


@router.get("/board/{public_id}")
def get_board_entry(public_id: str, db: Session = Depends(get_db), auth: Auth = Depends(require(P.BOARD_READ))):
    e = db.scalar(select(BoardEntry).where(BoardEntry.public_id == public_id))
    if e is None or e.status not in board.OWNER_VISIBLE:  # raw findings are diagnostics-only
        raise HTTPException(404, "Entry not found.")
    return {"entry": board.serialize(db, e), "history": board.entry_history(db, e.id)}


@router.get("/approvals")
def get_approvals(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return overview.approvals_view(db)


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: int, body: Decision, db: Session = Depends(get_wdb),
           auth: Auth = Depends(require(P.APPROVAL_DECIDE))):
    try:
        req = approvals.decide(db, auth.actor, approval_id, approve=body.approve, note=body.note)
    except approvals.ApprovalError as e:
        raise HTTPException(409, str(e)) from e
    return approvals.serialize(req)
