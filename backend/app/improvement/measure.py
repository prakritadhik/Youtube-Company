"""Reading one video's outcome figure for an experiment or a comparison, from retrieved (or owner-entered) metrics.

The rules are strict because a wrong number here would be read as evidence:

* the window starts on the day the video went public (Pacific date, as YouTube's daily figures are) and covers ``window_days`` days;
* a window counts only when it has ended at least two days ago (YouTube revises recent days) and **every day has a figure**: a missing day is *unknown*, never zero;
* a video that is private or not yet public has no value, and says why.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.community import Metric
from app.models.videos import Video
from app.timeutil import utcnow
from app.youtube.quota import PACIFIC

LAG_DAYS = 2
MAX_WINDOW = 90
# metric -> (source it is read from, aggregates that make sense for it)
METRICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "views": ("youtube_analytics", ("sum", "mean")),
    "estimatedMinutesWatched": ("youtube_analytics", ("sum", "mean")),
    "likes": ("youtube_analytics", ("sum", "mean")),
    "comments": ("youtube_analytics", ("sum", "mean")),
    "subscribersGained": ("youtube_analytics", ("sum", "mean")),
    "impressions": ("youtube_analytics", ("sum", "mean")),
    "averageViewPercentage": ("youtube_analytics", ("mean",)),
    "averageViewDuration": ("youtube_analytics", ("mean",)),
    "impressionsClickThroughRate": ("youtube_analytics", ("mean",)),
    "views_total": ("youtube_data", ("latest",)),
    "likes_total": ("youtube_data", ("latest",)),
    "comments_total": ("youtube_data", ("latest",)),
}
SOURCES = ("youtube_analytics", "youtube_data", "owner_entered")


class MeasureError(ValueError):
    pass


def check_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a metric specification. Raises ``MeasureError`` with a message for the owner."""
    metric = spec.get("metric")
    if metric not in METRICS:
        raise MeasureError(f"metric must be one of {', '.join(sorted(METRICS))}")
    default_source, aggregates = METRICS[metric]
    source = spec.get("source", default_source)
    if source not in SOURCES:
        raise MeasureError(f"source must be one of {', '.join(SOURCES)}")
    if source != "owner_entered" and source != default_source:
        raise MeasureError(f"{metric} is read from {default_source}, or entered by the owner")
    aggregate = spec.get("aggregate", aggregates[0])
    if aggregate not in aggregates:
        raise MeasureError(f"{metric} can be combined as: {', '.join(aggregates)}")
    window = spec.get("window_days", 7)
    if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= MAX_WINDOW:
        raise MeasureError(f"window_days must be a whole number from 1 to {MAX_WINDOW}")
    hib = spec.get("higher_is_better", True)
    if not isinstance(hib, bool):
        raise MeasureError("higher_is_better must be true or false")
    return {"metric": metric, "source": source, "aggregate": aggregate, "window_days": window, "higher_is_better": hib}


def start_day(v: Video) -> tuple[date | None, str, str | None]:
    """The Pacific date the video went public, how we know, and (when there is none) why not."""
    when: datetime | None = v.published_at
    basis = "recorded when the public upload was verified"
    if when is None and v.publish_at is not None and v.publish_at <= utcnow():
        when, basis = v.publish_at, "the scheduled time has passed (not confirmed public by this app)"
    if when is None:
        return None, "", "the video is not public yet (private uploads have no audience to measure)"
    return when.astimezone(PACIFIC).date(), basis, None


def measure_video(db: Session, v: Video, spec: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    today = today or utcnow().astimezone(PACIFIC).date()
    first, basis, why = start_day(v)
    out: dict[str, Any] = {"video_id": v.id, "title": v.title or v.working_title, "status": "unavailable", "value": None, "reason": why, "basis": basis or None}
    if first is None:
        return out
    last = first + timedelta(days=spec["window_days"] - 1)
    out["window"] = {"start": first.isoformat(), "end": last.isoformat()}
    if last > today - timedelta(days=LAG_DAYS):
        out.update(status="incomplete", reason=f"the {spec['window_days']}-day window ends {last.isoformat()}; figures are used only {LAG_DAYS} days after it ends, because YouTube revises recent days")
        return out
    rows = {m.day: m.value for m in db.scalars(select(Metric).where(Metric.video_id == v.id, Metric.metric == spec["metric"], Metric.source == spec["source"],
                                                                    Metric.day >= first, Metric.day <= last))}
    if spec["aggregate"] == "latest":
        if last not in rows:
            out.update(status="incomplete", reason=f"no reading was recorded on {last.isoformat()}, the last day of the window")
            return out
        out.update(status="complete", value=rows[last], reason=None)
        return out
    missing = [d.isoformat() for d in (first + timedelta(days=i) for i in range(spec["window_days"])) if d not in rows]
    if missing:
        out.update(status="incomplete", reason=f"no figure for {len(missing)} of {spec['window_days']} days ({', '.join(missing[:3])}{' …' if len(missing) > 3 else ''}); a missing day is unknown, never counted as zero")
        return out
    vals = list(rows.values())
    out.update(status="complete", value=round(sum(vals) if spec["aggregate"] == "sum" else sum(vals) / len(vals), 4), reason=None)
    return out
