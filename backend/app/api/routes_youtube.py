"""YouTube connection and upload routes. Owner-only for anything that changes what the app may do on YouTube."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Auth, require
from app.config import get_settings
from app.db import get_db, get_wdb
from app.models.videos import Video
from app.models.youtube import YouTubeUpload
from app.security.permissions import P
from app.services import settings as settings_svc
from app.youtube import connection as conn_svc
from app.youtube import quota, upload

router = APIRouter(prefix="/api", tags=["youtube"])


class ConfirmIn(BaseModel):
    google_channel_id: str = Field(min_length=3, max_length=64)
    made_for_kids: bool
    category_id: str | None = Field(default=None, max_length=4)


class ConnectIn(BaseModel):
    groups: list[str] = Field(default_factory=list, max_length=4)  # extra permissions to ask Google for: "analytics", "comments"


class UploadIn(BaseModel):
    privacy: str = Field(default="private", max_length=10)
    publish_at: datetime | None = None


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


@router.get("/youtube/status")
def youtube_status(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    from app.providers import base as providers

    state, detail = providers.capability_state(db, "youtube", "youtube.upload")
    return {**conn_svc.status(db), "quota": quota.summary(db), "upload_capability": {"state": state, "detail": detail},
            "operating_mode": settings_svc.operating_mode(db).value,
            "live_verified": False,
            "note": "This integration has been tested against a mock of Google's documented behaviour. Your first real connection and the self-test are what prove it."}


@router.post("/youtube/{channel}/connect")
def connect(channel: str, body: ConnectIn | None = None, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    s = get_settings()
    # localhost (not 127.0.0.1): the browser's session cookie belongs to the host name the dashboard was opened with
    return {"url": conn_svc.begin(db, auth.actor, channel, f"http://localhost:{s.port}", tuple(body.groups) if body else ())}


@router.get("/youtube/callback")
def callback(state: str = "", code: str = "", error: str = "", auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    """Google sends the owner's browser here. Always ends with a redirect back to the dashboard, with the outcome in the address."""
    home = get_settings().frontend_origin.rstrip("/") + "/settings"
    if error:
        return RedirectResponse(f"{home}?youtube=error&message={quote('Google reported: ' + error)}", status_code=303)
    try:
        slug = conn_svc.complete_sign_in(auth.actor, state, code)
    except conn_svc.YouTubeError as e:
        return RedirectResponse(f"{home}?youtube=error&message={quote(str(e))}", status_code=303)
    return RedirectResponse(f"{home}?youtube=signed_in&channel={quote(slug)}", status_code=303)


@router.post("/youtube/{channel}/confirm")
def confirm(channel: str, body: ConfirmIn, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    c = conn_svc.confirm(db, auth.actor, channel, google_channel_id=body.google_channel_id, made_for_kids=body.made_for_kids, category_id=body.category_id)
    return conn_svc.serialize(c)


@router.post("/youtube/{channel}/disconnect")
def disconnect(channel: str, auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    conn_svc.disconnect(auth.actor, channel)
    return {"ok": True}


@router.get("/videos/{video_id}/uploads")
def uploads(video_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    if db.get(Video, video_id) is None:
        raise HTTPException(404, "Video not found.")
    rows = db.scalars(select(YouTubeUpload).where(YouTubeUpload.video_id == video_id).order_by(YouTubeUpload.id.desc()))
    return {"uploads": [upload.serialize(u) for u in rows]}


@router.post("/videos/{video_id}/upload")
def queue_upload(video_id: int, body: UploadIn, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return upload.serialize(upload.prepare(db, auth.actor, video_id, privacy=body.privacy, publish_at=body.publish_at))


@router.post("/youtube/uploads/{upload_id}/abandon")
def abandon_upload(upload_id: int, body: NoteIn, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    return upload.serialize(upload.abandon(db, auth.actor, upload_id, body.note))
