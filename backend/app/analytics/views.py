"""Read models for the Performance and Comments pages. Every figure says where it came from and when; a missing figure says why."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import retention as retention_analysis
from app.analytics.collect import REVENUE_NOTE
from app.models.community import Comment, Metric, MetricGap, ReplyDraft, RetentionPoint
from app.models.org import Channel
from app.models.videos import Video
from app.timeutil import iso
from app.youtube import connection as conn_svc

# (metric, label, source, additive over days)
EXPECTED: list[tuple[str, str, str, bool]] = [
    ("views_total", "Total views so far", "youtube_data", False),
    ("likes_total", "Total likes so far", "youtube_data", False),
    ("comments_total", "Total comments so far", "youtube_data", False),
    ("views", "Views per day", "youtube_analytics", True),
    ("estimatedMinutesWatched", "Minutes watched per day", "youtube_analytics", True),
    ("averageViewDuration", "Average view duration (seconds)", "youtube_analytics", False),
    ("averageViewPercentage", "Average percentage viewed", "youtube_analytics", False),
    ("likes", "Likes per day", "youtube_analytics", True),
    ("comments", "Comments per day", "youtube_analytics", True),
    ("subscribersGained", "Subscribers gained per day", "youtube_analytics", True),
    ("subscribersLost", "Subscribers lost per day", "youtube_analytics", True),
    ("impressions", "Impressions per day", "youtube_analytics", True),
    ("impressionsClickThroughRate", "Click-through rate (%)", "youtube_analytics", False),
]
SOURCE_NAMES = {"youtube_data": "YouTube Data API", "youtube_analytics": "YouTube Analytics API", "owner_entered": "entered by the owner"}


def video_performance(db: Session, video_id: int) -> dict[str, Any]:
    v = db.get(Video, video_id)
    if v is None:
        raise LookupError("video not found")
    if not v.youtube_video_id:
        why = "this video has not been uploaded to YouTube, so it has no performance data"
        return {"video_id": v.id, "uploaded": False, "metrics": [{"metric": m, "label": lab, "status": "unavailable", "reason": why} for m, lab, *_ in EXPECTED]
                + [{"metric": "retention", "label": "Audience retention", "status": "unavailable", "reason": why}, _revenue()], "retention": [],
                "retention_analysis": {"status": "unavailable", "reason": why}}
    rows: dict[tuple[str, str], list[Metric]] = {}
    for m in db.scalars(select(Metric).where(Metric.video_id == video_id).order_by(Metric.day)):
        rows.setdefault((m.metric, m.source), []).append(m)
    gaps = {(g.metric, g.source): g for g in db.scalars(select(MetricGap).where(MetricGap.video_id == video_id))}
    out: list[dict[str, Any]] = []
    for metric, label, source, additive in EXPECTED:
        got = rows.get((metric, source))
        if got:
            series = [{"day": str(m.day), "value": m.value} for m in got]
            item: dict[str, Any] = {"metric": metric, "label": label, "status": "available", "source": SOURCE_NAMES[source], "latest": series[-1], "series": series,
                                    "retrieved_at": iso(max(m.retrieved_at for m in got))}
            if additive:
                item["total_in_window"] = sum(s["value"] for s in series)
            out.append(item)
            continue
        g = gaps.get((metric, source))
        out.append({"metric": metric, "label": label, "status": "unavailable", "source": SOURCE_NAMES[source],
                    "reason": g.reason if g else "not collected yet (the first collection runs daily, or use Collect now)", "last_attempt": iso(g.last_attempt) if g else None})
    pts = db.scalars(select(RetentionPoint).where(RetentionPoint.video_id == video_id).order_by(RetentionPoint.ratio)).all()
    rg = gaps.get(("audienceWatchRatio", "youtube_analytics"))
    retention = [{"ratio": p.ratio, "audience_watch_ratio": p.audience_watch_ratio, "relative": p.relative_retention} for p in pts]
    out.append({"metric": "retention", "label": "Audience retention", "status": "available" if pts else "unavailable", "source": SOURCE_NAMES["youtube_analytics"],
                "retrieved_at": iso(max(p.retrieved_at for p in pts)) if pts else None,
                **({} if pts else {"reason": rg.reason if rg else "not collected yet (the first collection runs daily, or use Collect now)"})})
    out.append(_revenue())
    analysis = (retention_analysis.analyse([(p.ratio, p.audience_watch_ratio, p.relative_retention) for p in pts]) if pts
                else {"status": "unavailable", "reason": rg.reason if rg else "no retention data has been collected yet"})
    return {"video_id": v.id, "title": v.title or v.working_title, "uploaded": True, "youtube_video_id": v.youtube_video_id, "metrics": out, "retention": retention,
            "retention_analysis": analysis,
            "caveat": "Figures are as reported by YouTube at the time shown. Recent days may be revised by YouTube, and small numbers say little."}


def _revenue() -> dict[str, Any]:
    return {"metric": "revenue", "label": "Revenue", "status": "unavailable", "source": "none", "reason": REVENUE_NOTE}


def overview(db: Session) -> dict[str, Any]:
    chans = []
    for ch in db.scalars(select(Channel).order_by(Channel.id)):
        conn = conn_svc.connection_for(db, ch.id)
        vids = db.scalars(select(Video).where(Video.channel_id == ch.id, Video.youtube_video_id.is_not(None)).order_by(Video.id)).all()
        ids = [v.id for v in vids]
        latest_total = None
        last = None
        if ids:
            last = db.scalar(select(func.max(Metric.retrieved_at)).where(Metric.video_id.in_(ids)))
            # sum of each video's latest cumulative view count, only over videos that have one
            per_video = {}
            for m in db.scalars(select(Metric).where(Metric.video_id.in_(ids), Metric.metric == "views_total").order_by(Metric.day)):
                per_video[m.video_id] = m.value
            if per_video:
                latest_total = {"value": sum(per_video.values()), "videos_counted": len(per_video), "videos_uploaded": len(ids)}
        gap_count = db.scalar(select(func.count()).select_from(MetricGap).where(MetricGap.channel_id == ch.id)) or 0
        by_risk = dict(db.execute(select(Comment.risk, func.count()).where(Comment.channel_id == ch.id, Comment.status.in_(("NEW", "TRIAGED", "DRAFTED"))).group_by(Comment.risk)).all())
        chans.append({"channel": ch.slug, "name": ch.name, "connection": conn_svc.serialize(conn), "uploaded_videos": len(ids), "last_collected": iso(last) if last else None,
                      "total_views": latest_total if latest_total else {"status": "unavailable", "reason": "no view count has been collected for this channel yet"},
                      "open_gaps": gap_count, "comments_waiting": by_risk,
                      "videos": [{"id": v.id, "title": v.title or v.working_title, "youtube_video_id": v.youtube_video_id} for v in vids]})
    return {"channels": chans, "revenue": {"status": "unavailable", "reason": REVENUE_NOTE}}


def comment_view(c: Comment, video_title: str | None = None, channel_slug: str | None = None, draft: ReplyDraft | None = None) -> dict[str, Any]:
    from app.community import replies

    return {"id": c.id, "video_id": c.video_id, "video_title": video_title, "channel": channel_slug, "author": c.author, "text": c.text, "untrusted": True,
            "like_count": c.like_count, "published_at": iso(c.published_at), "category": c.category, "category_method": c.category_method, "risk": c.risk, "reasons": c.reasons,
            "injection_flags": c.injection_flags, "status": c.status, "is_reply": c.parent_youtube_id is not None, "is_ours": c.is_ours,
            "draft": replies.serialize(draft) if draft else None}


SENTIMENT = {"praise": "positive", "criticism": "negative", "abuse": "negative", "question": "questions",
             "safety": "needs_a_person", "copyright": "needs_a_person", "medical_concern": "needs_a_person", "personal_data": "needs_a_person"}


def comments_summary(db: Session) -> dict[str, Any]:
    by_cat = dict(db.execute(select(Comment.category, func.count()).where(Comment.is_ours.is_(False), Comment.category != "spam").group_by(Comment.category)).all())
    sentiment: dict[str, int] = {"positive": 0, "negative": 0, "questions": 0, "needs_a_person": 0, "unclassified": 0}
    for cat, n in by_cat.items():
        sentiment[SENTIMENT.get(cat, "unclassified")] += n
    return {"sentiment": sentiment, "by_category": dict(db.execute(select(Comment.category, func.count()).where(Comment.is_ours.is_(False)).group_by(Comment.category)).all()),
            "by_status": dict(db.execute(select(Comment.status, func.count()).where(Comment.is_ours.is_(False)).group_by(Comment.status)).all()),
            "escalated_waiting": db.scalar(select(func.count()).select_from(Comment).where(Comment.risk == "escalate", Comment.status.in_(("NEW", "TRIAGED", "DRAFTED")))) or 0,
            "drafts_waiting": db.scalar(select(func.count()).select_from(ReplyDraft).where(ReplyDraft.status == "DRAFT")) or 0,
            "method": "rules-v1: fixed rules, English only, blunt. Anything it cannot judge is marked for a person."}
