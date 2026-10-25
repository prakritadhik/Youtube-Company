"""Collecting real performance data from YouTube, and being honest about what could not be collected.

Three sources, each stored under its own name so nothing is mixed up:

* ``youtube_data``      cumulative views, likes and comments as of the day of retrieval (``videos.list`` statistics; needs only
                        the permissions already granted);
* ``youtube_analytics`` daily views, watch time, average view duration, likes, comments, subscribers and (where YouTube offers
                        them) impressions and click-through rate, plus the audience-retention curve (needs the *analytics*
                        permission);
* ``owner_entered``     numbers the owner typed in, with their source.

A metric that could not be retrieved is *not* stored as zero. A ``MetricGap`` row records Google's reason (permission not granted,
not offered for this channel, no rows yet) and the dashboard shows "unavailable: <reason>". Revenue is never requested: it needs a
separate monetisation permission and a monetised channel, and is reported as unavailable.

Network calls never run inside a database write transaction.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models.community import Metric, MetricGap, RetentionPoint
from app.models.org import Channel
from app.models.videos import Video
from app.models.youtube import YouTubeConnection
from app.storage import write_json_artifact
from app.timeutil import iso, utcnow
from app.youtube import client as yt
from app.youtube import connection as conn_svc
from app.youtube import quota

WINDOW_DAYS = 28
DAILY_CORE = ["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "likes", "comments", "subscribersGained", "subscribersLost"]
DAILY_REACH = ["impressions", "impressionsClickThroughRate"]  # requested separately: if YouTube does not offer them, the core metrics still arrive
STAT_FIELDS = {"viewCount": "views_total", "likeCount": "likes_total", "commentCount": "comments_total"}
REVENUE_NOTE = "Revenue is not collected: it needs a separate monetisation permission and a monetised channel."


class CollectError(Exception):
    pass


def pacific_today(now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(quota.PACIFIC).date()


def _envelope(endpoint: str, request: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    return {"provider": "youtube", "endpoint": endpoint, "status_code": 200, "simulated": False, "retrieved_at": iso(utcnow()), "asked_for": request, "response": response}


def _rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    names = [h["name"] for h in report.get("columnHeaders", [])]
    return [dict(zip(names, r, strict=False)) for r in report.get("rows") or []]


def _upsert(db: Session, *, channel_id: int, video_id: int | None, metric: str, day: date, value: float, source: str, ref: str | None, now: datetime) -> None:
    m = db.scalar(select(Metric).where(Metric.scope == ("video" if video_id else "channel"), Metric.channel_id == channel_id, Metric.video_id == video_id,
                                       Metric.metric == metric, Metric.day == day, Metric.source == source))
    if m is None:
        db.add(Metric(scope="video" if video_id else "channel", channel_id=channel_id, video_id=video_id, metric=metric, day=day, value=float(value), source=source,
                      retrieved_at=now, response_ref=ref))
    else:
        m.value, m.retrieved_at, m.response_ref = float(value), now, ref


def _gap(db: Session, channel_id: int, video_id: int | None, metric: str, source: str, reason: str, now: datetime) -> None:
    g = db.scalar(select(MetricGap).where(MetricGap.channel_id == channel_id, MetricGap.video_id == video_id, MetricGap.metric == metric, MetricGap.source == source))
    if g is None:
        db.add(MetricGap(channel_id=channel_id, video_id=video_id, metric=metric, source=source, reason=reason[:500], last_attempt=now))
    else:
        g.reason, g.last_attempt, g.attempts = reason[:500], now, g.attempts + 1


def _clear_gaps(db: Session, channel_id: int, video_id: int | None, metrics: list[str], source: str) -> None:
    db.execute(delete(MetricGap).where(MetricGap.channel_id == channel_id, MetricGap.video_id == video_id, MetricGap.metric.in_(metrics), MetricGap.source == source))


def _reason(e: yt.YouTubeApiError) -> str:
    if e.status == 403:
        return f"YouTube refused ({e.reason}): the permission may be missing or the data is not offered for this channel. {e.message}"
    if e.status == 400:
        return f"YouTube does not offer this for this channel or period ({e.reason}): {e.message}"
    return f"{e.status} {e.reason}: {e.message}"


def collect_video(video_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Fetch and store everything available for one uploaded video. Returns a summary of what arrived and what did not."""
    now = now or utcnow()
    with session_scope(write=False) as db:
        v = db.get(Video, video_id)
        if v is None or not v.youtube_video_id:
            raise CollectError("this video has no YouTube id (it has not been uploaded)")
        conn = conn_svc.connection_for(db, v.channel_id)
        if conn is None or conn.status != "CONNECTED":
            raise CollectError("this channel is not connected to YouTube")
        quota.require(db, "videos.list")
        yid, channel_id, conn_id = v.youtube_video_id, v.channel_id, conn.id
        analytics_ok = conn_svc.has_group(conn, "analytics")
    token = conn_svc.access_token(conn_id)
    api = yt.YouTubeClient(token, transport=conn_svc.TRANSPORT)
    out: dict[str, Any] = {"video_id": video_id, "stats": False, "analytics_rows": 0, "retention_points": 0, "gaps": []}
    to_store: list[dict[str, Any]] = []
    gaps: list[tuple[str, str, str]] = []  # metric, source, reason
    cleared: list[tuple[list[str], str]] = []
    refs: dict[str, str] = {}

    try:  # cumulative counts (works with the permissions already granted)
        stats = api.video_statistics([yid])
        with session_scope() as db:
            quota.record(db, "videos.list", video_id=video_id, note="statistics")
        items = stats.get("items") or []
        if items:
            refs["stats"] = write_json_artifact(f"youtube/metrics/{video_id}", f"statistics-{now.strftime('%Y%m%dT%H%M%S')}.json",
                                                _envelope("videos.list (statistics)", {"id": yid}, stats))
            st = items[0].get("statistics", {})
            for field, name in STAT_FIELDS.items():
                if field in st:
                    to_store.append({"metric": name, "day": pacific_today(now), "value": float(st[field]), "source": "youtube_data", "ref": refs["stats"]})
                else:  # e.g. comments disabled, or likes hidden: not zero, unavailable
                    gaps.append((name, "youtube_data", f"YouTube did not report '{field}' for this video (it may be hidden or disabled)."))
            cleared.append(([n for f, n in STAT_FIELDS.items() if f in st], "youtube_data"))
            out["stats"] = True
        else:
            gaps.append(("views_total", "youtube_data", "YouTube returned no video for this id (it may have been deleted or is not visible to this account)."))
    except yt.YouTubeApiError as e:
        with session_scope() as db:
            quota.record(db, "videos.list", ok=False, video_id=video_id, note=f"statistics: {e.reason}")
        gaps.append(("views_total", "youtube_data", _reason(e)))

    start, end = (pacific_today(now) - timedelta(days=WINDOW_DAYS)).isoformat(), (pacific_today(now) - timedelta(days=1)).isoformat()
    if not analytics_ok:
        for m in [*DAILY_CORE, *DAILY_REACH, "audienceWatchRatio"]:
            gaps.append((m, "youtube_analytics", "permission not granted: allow analytics for this channel in Settings, YouTube."))
    else:
        for group in (DAILY_CORE, DAILY_REACH):
            try:
                rep = api.analytics_report(start=start, end=end, metrics=",".join(group), dimensions="day", filters=f"video=={yid}", sort="day")
            except yt.YouTubeApiError as e:
                gaps.extend((m, "youtube_analytics", _reason(e)) for m in group)
                continue
            ref = write_json_artifact(f"youtube/metrics/{video_id}", f"analytics-{group[0]}-{now.strftime('%Y%m%dT%H%M%S')}.json",
                                      _envelope("analytics.reports.query", {"metrics": group, "start": start, "end": end}, rep))
            rows = _rows(rep)
            if not rows:
                gaps.extend((m, "youtube_analytics", "YouTube returned no rows for this period (data for new or small videos can lag by days).") for m in group)
                continue
            for r in rows:
                d = date.fromisoformat(str(r["day"]))
                for m in group:
                    if r.get(m) is not None:
                        to_store.append({"metric": m, "day": d, "value": float(r[m]), "source": "youtube_analytics", "ref": ref})
            cleared.append((group, "youtube_analytics"))
            out["analytics_rows"] += len(rows)
        try:
            rep = api.analytics_report(start=start, end=end, metrics="audienceWatchRatio,relativeRetentionPerformance", dimensions="elapsedVideoTimeRatio", filters=f"video=={yid}")
            ref = write_json_artifact(f"youtube/metrics/{video_id}", f"retention-{now.strftime('%Y%m%dT%H%M%S')}.json",
                                      _envelope("analytics.reports.query (retention)", {"video": yid, "start": start, "end": end}, rep))
            pts = _rows(rep)
            if pts:
                refs["retention"] = ref
                to_store.append({"retention": [(float(p["elapsedVideoTimeRatio"]), float(p["audienceWatchRatio"]), p.get("relativeRetentionPerformance")) for p in pts]})
                cleared.append((["audienceWatchRatio"], "youtube_analytics"))
                out["retention_points"] = len(pts)
            else:
                gaps.append(("audienceWatchRatio", "youtube_analytics", "YouTube returned no retention data yet (it needs enough views)."))
        except yt.YouTubeApiError as e:
            gaps.append(("audienceWatchRatio", "youtube_analytics", _reason(e)))

    with session_scope() as db:
        for item in to_store:
            if "retention" in item:
                db.execute(delete(RetentionPoint).where(RetentionPoint.video_id == video_id))
                for ratio, watch, rel in item["retention"]:
                    db.add(RetentionPoint(video_id=video_id, ratio=ratio, audience_watch_ratio=watch, relative_retention=None if rel is None else float(rel), retrieved_at=now))
            else:
                _upsert(db, channel_id=channel_id, video_id=video_id, metric=item["metric"], day=item["day"], value=item["value"], source=item["source"], ref=item["ref"], now=now)
        for metrics, source in cleared:
            _clear_gaps(db, channel_id, video_id, metrics, source)
        for metric, source, reason in gaps:
            _gap(db, channel_id, video_id, metric, source, reason, now)
    out["gaps"] = [{"metric": m, "source": s, "reason": r} for m, s, r in gaps]
    out["stored_rows"] = sum(1 for i in to_store if "retention" not in i)
    return out


def collect_all(*, now: datetime | None = None) -> dict[str, Any]:
    """Every uploaded video on every connected channel. One video failing never stops the rest."""
    with session_scope(write=False) as db:
        conns = [c for c in db.scalars(select(YouTubeConnection).where(YouTubeConnection.status == "CONNECTED"))]
        if not conns:
            return {"connected_channels": 0, "videos": [], "note": "No YouTube channel is connected, so no performance data can be collected."}
        vids = [(v.id, v.channel_id) for v in db.scalars(select(Video).where(Video.youtube_video_id.is_not(None)).order_by(Video.id))
                if any(c.channel_id == v.channel_id for c in conns)]
    results: list[dict[str, Any]] = []
    for vid, _ch in vids:
        try:
            results.append(collect_video(vid, now=now))
        except (CollectError, conn_svc.YouTubeError, quota.QuotaExceeded) as e:
            results.append({"video_id": vid, "error": str(e)})
    return {"connected_channels": len(conns), "videos": results, "note": REVENUE_NOTE}


def channel_name(db: Session, channel_id: int) -> str:
    return db.get(Channel, channel_id).name  # type: ignore[union-attr]
