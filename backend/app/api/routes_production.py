from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Auth, require
from app.db import get_db, get_wdb
from app.models.production import OriginalityReview, QAReport
from app.models.videos import Video
from app.production import assets as assets_svc
from app.production import views
from app.providers import base as providers
from app.security.permissions import P
from app.services import audit
from app.storage import UnsafePath

router = APIRouter(prefix="/api", tags=["production"])

_MEDIA = {"final": ("final_video_path", "video/mp4"), "thumbnail": ("thumbnail_path", "image/png")}


@router.get("/videos/{video_id}")
def video_detail(video_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    d = views.video_detail(db, video_id)
    if d is None:
        raise HTTPException(404, "Video not found.")
    return d


@router.get("/videos/{video_id}/media/{kind}")
def video_media(video_id: int, kind: str, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    """Stream the video or thumbnail to the signed-in owner. The path comes from the database, never from the caller."""
    if kind not in _MEDIA:
        raise HTTPException(404, "Unknown media.")
    v = db.get(Video, video_id)
    attr, mime = _MEDIA[kind]
    rel = getattr(v, attr, None) if v else None
    if not rel:
        raise HTTPException(404, "No such file yet.")
    try:
        path = assets_svc.resolve_asset_path(rel)
    except UnsafePath as e:
        raise HTTPException(404, "No such file.") from e
    if not path.is_file():
        raise HTTPException(404, "The file is missing.")
    return FileResponse(path, media_type=mime, headers={"Content-Disposition": "inline"})


@router.get("/providers")
def provider_status(db: Session = Depends(get_db), auth: Auth = Depends(require(P.SETTINGS_READ))):
    return {"providers": providers.status_report(db),
            "note": "A capability is ready only when it is implemented, configured, attested by you, within budget, and the company is not paused."}


@router.get("/diagnostics/videos/{video_id}/originality")
def full_originality(video_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.DIAG_READ))):
    r = db.scalar(select(OriginalityReview).where(OriginalityReview.video_id == video_id).order_by(OriginalityReview.version.desc()))
    if r is None:
        raise HTTPException(404, "No originality review yet.")
    audit.record(db, auth.actor, "diagnostics.viewed", "originality_review", r.id, {"video": video_id})
    return {"version": r.version, "status": r.status, "report": r.report, "artifact": r.artifact_path}


@router.get("/diagnostics/videos/{video_id}/qa")
def full_qa(video_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.DIAG_READ))):
    q = db.scalar(select(QAReport).where(QAReport.video_id == video_id).order_by(QAReport.version.desc()))
    if q is None:
        raise HTTPException(404, "No QA report yet.")
    audit.record(db, auth.actor, "diagnostics.viewed", "qa_report", q.id, {"video": video_id})
    return {"version": q.version, "passed": q.passed, "checks": q.checks, "artifact": q.artifact_path}
