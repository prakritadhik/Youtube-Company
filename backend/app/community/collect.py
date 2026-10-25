"""Fetching public comments for uploaded videos and sorting them. Nothing here ever writes to YouTube.

Comment text and author names are untrusted: they are cleaned and capped before storage, scanned for instruction-like text, and
only ever shown quoted. New comments are triaged once with ``rules-v1``; a comment seen again only has its like count refreshed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.community import triage as tri
from app.db import session_scope
from app.models.community import Comment, MetricGap
from app.models.org import Channel
from app.models.videos import Video
from app.models.youtube import YouTubeConnection
from app.research import untrusted
from app.storage import write_json_artifact
from app.timeutil import iso, utcnow
from app.youtube import client as yt
from app.youtube import connection as conn_svc
from app.youtube import quota

MAX_PAGES = 3  # 100 threads per page; the newest are fetched first, older ones are not chased every run
MAX_TEXT = 2000
MAX_AUTHOR = 100


def _when(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return utcnow()


def _flatten(thread: dict[str, Any]) -> list[dict[str, Any]]:
    """The top-level comment and any replies YouTube included, as plain dicts."""
    out = []
    top = (thread.get("snippet") or {}).get("topLevelComment") or {}
    items = [top, *((thread.get("replies") or {}).get("comments") or [])]
    for c in items:
        s = c.get("snippet") or {}
        if not c.get("id"):
            continue
        out.append({"id": c["id"], "parent": s.get("parentId"), "author": s.get("authorDisplayName", ""),
                    "author_channel": ((s.get("authorChannelId") or {}).get("value")), "text": s.get("textOriginal") or s.get("textDisplay") or "",
                    "likes": int(s.get("likeCount") or 0), "published": s.get("publishedAt")})
    return out


def _gap(db, channel_id: int, video_id: int, reason: str, now: datetime) -> None:
    g = db.scalar(select(MetricGap).where(MetricGap.channel_id == channel_id, MetricGap.video_id == video_id, MetricGap.metric == "comments", MetricGap.source == "youtube_comments"))
    if g is None:
        db.add(MetricGap(channel_id=channel_id, video_id=video_id, metric="comments", source="youtube_comments", reason=reason[:500], last_attempt=now))
    else:
        g.reason, g.last_attempt, g.attempts = reason[:500], now, g.attempts + 1


def collect_video(video_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    with session_scope(write=False) as db:
        v = db.get(Video, video_id)
        if v is None or not v.youtube_video_id:
            raise conn_svc.YouTubeError("NOT_UPLOADED", "this video has no YouTube id (it has not been uploaded)")
        conn = conn_svc.connection_for(db, v.channel_id)
        if conn is None or conn.status != "CONNECTED":
            raise conn_svc.YouTubeError("NOT_CONNECTED", "this channel is not connected to YouTube")
        channel_id, yid, conn_id, ours = v.channel_id, v.youtube_video_id, conn.id, conn.google_channel_id
        kind = db.get(Channel, channel_id).kind  # type: ignore[union-attr]
        permitted = conn_svc.has_group(conn, "comments")
    out: dict[str, Any] = {"video_id": video_id, "fetched": 0, "new": 0, "by_category": {}, "gap": None}
    if not permitted:
        reason = "permission not granted: allow comments for this channel in Settings, YouTube."
        with session_scope() as db:
            _gap(db, channel_id, video_id, reason, now)
        out["gap"] = reason
        return out
    api = yt.YouTubeClient(conn_svc.access_token(conn_id), transport=conn_svc.TRANSPORT)
    pages: list[dict[str, Any]] = []
    token: str | None = None
    gap: str | None = None
    for _ in range(MAX_PAGES):
        with session_scope(write=False) as db:
            quota.require(db, "commentThreads.list")
        try:
            page = api.comment_threads(yid, page_token=token)
        except yt.YouTubeApiError as e:
            with session_scope() as db:
                quota.record(db, "commentThreads.list", ok=False, video_id=video_id, note=e.reason)
            if e.reason == "commentsDisabled":
                gap = "comments are turned off for this video."
            elif e.is_quota:
                gap = "YouTube's daily quota is used up; comments will be fetched again after it resets."
            else:
                gap = f"{e.status} {e.reason}: {e.message}"
            break
        with session_scope() as db:
            quota.record(db, "commentThreads.list", video_id=video_id, note="page")
        pages.append(page)
        token = page.get("nextPageToken")
        if not token:
            break
    ref = None
    if pages:
        ref = write_json_artifact(f"youtube/comments/{video_id}", f"threads-{now.strftime('%Y%m%dT%H%M%S')}.json",
                                  {"provider": "youtube", "endpoint": "commentThreads.list", "status_code": 200, "simulated": False, "retrieved_at": iso(now), "pages": pages})
    flat = [c for p in pages for t in p.get("items", []) for c in _flatten(t)]
    with session_scope() as db:
        known = {c.youtube_comment_id: c for c in db.scalars(select(Comment).where(Comment.video_id == video_id))}
        for c in flat:
            out["fetched"] += 1
            existing = known.get(c["id"])
            if existing is not None:
                existing.like_count, existing.retrieved_at = c["likes"], now
                continue
            text, _ = untrusted.clean(c["text"], MAX_TEXT)
            author, _ = untrusted.clean(c["author"], MAX_AUTHOR)
            is_ours = bool(ours and c["author_channel"] == ours)
            t = tri.triage(text, channel_kind=kind, is_ours=is_ours)
            db.add(Comment(youtube_comment_id=c["id"], video_id=video_id, channel_id=channel_id, parent_youtube_id=c["parent"], author=author, author_channel_id=c["author_channel"],
                           is_ours=is_ours, text=text, like_count=c["likes"], published_at=_when(c["published"]), category=t.category, category_method=t.method, risk=t.risk,
                           reasons=t.reasons, injection_flags=t.injection_flags, status="IGNORED" if t.category in ("spam", "own_comment") else "TRIAGED", retrieved_at=now))
            out["new"] += 1
            out["by_category"][t.category] = out["by_category"].get(t.category, 0) + 1
        if gap is None:
            db.query(MetricGap).filter_by(channel_id=channel_id, video_id=video_id, metric="comments", source="youtube_comments").delete()
        else:
            _gap(db, channel_id, video_id, gap, now)
    out["gap"], out["response_ref"] = gap, ref
    return out


def collect_all(*, now: datetime | None = None) -> dict[str, Any]:
    with session_scope(write=False) as db:
        conns = list(db.scalars(select(YouTubeConnection).where(YouTubeConnection.status == "CONNECTED")))
        vids = [v.id for v in db.scalars(select(Video).where(Video.youtube_video_id.is_not(None)).order_by(Video.id)) if any(c.channel_id == v.channel_id for c in conns)]
    if not conns:
        return {"connected_channels": 0, "videos": [], "note": "No YouTube channel is connected, so no comments can be fetched."}
    results = []
    for vid in vids:
        try:
            results.append(collect_video(vid, now=now))
        except (conn_svc.YouTubeError, quota.QuotaExceeded) as e:
            results.append({"video_id": vid, "error": str(e)})
    return {"connected_channels": len(conns), "videos": results}
