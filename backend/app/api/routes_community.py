"""Performance data, comments and reply drafts. Reads are open to the dashboard; anything that can lead to a public reply needs a manager or the owner,
and the reply itself still needs the owner's approval (or the owner's explicit auto-reply policy)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import views
from app.api.deps import Auth, require
from app.community import replies
from app.community.replies import ReplyError
from app.db import get_db, get_wdb
from app.models.community import Comment, ReplyDraft
from app.models.org import Channel
from app.models.videos import Video
from app.security.permissions import P
from app.services import audit, tasks
from app.timeutil import utcnow

router = APIRouter(prefix="/api", tags=["analytics", "community"])


class CollectIn(BaseModel):
    what: str = Field(pattern="^(analytics|comments)$")


class DraftIn(BaseModel):
    text: str | None = Field(default=None, max_length=replies.MAX_REPLY + 200)


@router.get("/analytics/overview")
def analytics_overview(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return views.overview(db)


@router.get("/videos/{video_id}/performance")
def video_performance(video_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    try:
        return views.video_performance(db, video_id)
    except LookupError:
        raise HTTPException(404, "Video not found.") from None


@router.post("/analytics/collect")
def collect_now(body: CollectIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    """Queue a collection now instead of waiting for the schedule. Reads only. One per minute per kind."""
    task_type, title = (("analytics.collect", "Collect performance data (requested)") if body.what == "analytics"
                        else ("community.collect_comments", "Fetch and sort comments (requested)"))
    t = tasks.create(db, auth.actor, title=title, task_type=task_type, review_required=False, idempotency_key=f"manual:{task_type}:{utcnow():%Y%m%d%H%M}")
    audit.record(db, auth.actor, "analytics.collect_requested", "task", t.id, {"what": body.what})
    return {"task_id": t.id, "status": t.status}


@router.get("/comments")
def list_comments(status: str | None = None, category: str | None = None, risk: str | None = None, channel: str | None = None, video_id: int | None = None,
                  limit: int = 100, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    limit = max(1, min(limit, 300))
    q = select(Comment, func.coalesce(Video.title, Video.working_title), Channel.slug).join(Video, Video.id == Comment.video_id).join(Channel, Channel.id == Comment.channel_id).where(Comment.is_ours.is_(False))
    for col, val in ((Comment.status, status), (Comment.category, category), (Comment.risk, risk), (Channel.slug, channel), (Comment.video_id, video_id)):
        if val is not None:
            q = q.where(col == val)
    # escalations first, then newest
    order = {"escalate": 0, "review": 1, "low": 2}
    rows = db.execute(q.order_by(Comment.published_at.desc()).limit(limit * 3)).all()
    rows.sort(key=lambda r: order.get(r[0].risk, 3))
    drafts = {d.comment_id: d for d in db.scalars(select(ReplyDraft).where(ReplyDraft.status.in_(("DRAFT", "APPROVED", "SENDING", "SENT", "FAILED"))).order_by(ReplyDraft.id))}
    return {"comments": [views.comment_view(c, title, slug, drafts.get(c.id)) for c, title, slug in rows[:limit]], "summary": views.comments_summary(db)}


@router.post("/comments/{comment_id}/draft")
def draft_reply(comment_id: int, body: DraftIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return replies.serialize(replies.create_draft(db, auth.actor, comment_id, body.text))


@router.post("/comments/{comment_id}/ignore")
def ignore_comment(comment_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    c = db.get(Comment, comment_id)
    if c is None:
        raise HTTPException(404, "Comment not found.")
    if c.status in ("DRAFTED", "REPLIED"):
        raise ReplyError("HAS_REPLY", "This comment has a reply in progress; reject the draft first.")
    c.status = "IGNORED"
    audit.record(db, auth.actor, "community.comment_ignored", "comment", c.id, {})
    return {"id": c.id, "status": c.status}


@router.post("/reply-drafts/{draft_id}/submit")
def submit_draft(draft_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return replies.submit(db, auth.actor, draft_id)


@router.post("/reply-drafts/{draft_id}/discard")
def discard_draft(draft_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    return replies.serialize(replies.discard(db, auth.actor, draft_id))
