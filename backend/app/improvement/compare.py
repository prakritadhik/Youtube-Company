"""Comparing videos on one channel with the same measurement, so the numbers can be set side by side.

This is description, not proof. A table of videos shows which did better on a figure; it cannot show *why*, and a single video's good number may be
luck, timing or YouTube's recommendation. Every comparison therefore:

* uses the same window and rules as the experiments (a video appears only with complete figures, otherwise with the reason it does not);
* never mixes channels (the audiences differ);
* labels the spread of the channel's own results, so a "winner" can be read against how much videos normally vary;
* says that it is not an experiment.
"""

from __future__ import annotations

from datetime import date
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import VideoStage
from app.improvement import measure
from app.models.improvement import Experiment, ExperimentVideo
from app.models.org import Channel
from app.models.videos import Video

CAVEATS = [
    "This compares videos; it is not an experiment. A difference between two videos can come from topic, timing, season or YouTube's own recommendation, not from the thing you are looking at.",
    "A video's number is read against how much this channel's videos normally vary (the range below), not against zero.",
    "Videos younger than the window, or with missing days, are listed with the reason and left out of the summary.",
]


def compare(db: Session, channel_slug: str, spec_in: dict[str, Any], *, today: date | None = None, limit: int = 50) -> dict[str, Any]:
    ch = db.scalar(select(Channel).where(Channel.slug == channel_slug))
    if ch is None:
        raise measure.MeasureError("unknown channel")
    spec = measure.check_spec(spec_in)
    vids = list(db.scalars(select(Video).where(Video.channel_id == ch.id, Video.stage.in_((VideoStage.PUBLISHED.value, VideoStage.SCHEDULED.value)))
                           .order_by(Video.published_at.desc().nulls_last(), Video.id.desc()).limit(limit)))
    arms = {r.video_id: (e.public_id, r.arm) for r, e in db.execute(select(ExperimentVideo, Experiment).join(Experiment, Experiment.id == ExperimentVideo.experiment_id))}
    table = []
    for v in vids:
        m = measure.measure_video(db, v, spec, today=today)
        exp = arms.get(v.id)
        table.append({**m, "published_at": v.published_at.isoformat() if v.published_at else None, "experiment": exp[0] if exp else None, "arm": exp[1] if exp else None})
    done = [t["value"] for t in table if t["status"] == "complete"]
    summary: dict[str, Any] = {"n": len(done), "of": len(table)}
    if done:
        summary.update(median=round(median(done), 4), mean=round(mean(done), 4), min=min(done), max=max(done))
        for t in table:
            if t["status"] == "complete" and summary["median"]:
                t["versus_median_percent"] = round(100 * (t["value"] - summary["median"]) / abs(summary["median"]), 1)
        ranked = sorted((t for t in table if t["status"] == "complete"), key=lambda t: t["value"], reverse=spec["higher_is_better"])
        for i, t in enumerate(ranked, 1):
            t["rank"] = i
    if len(done) < 5:
        summary["note"] = f"Only {len(done)} video(s) have complete figures, too few to say what is normal for this channel; read the numbers as individual observations."
    return {"channel": ch.slug, "metric": spec, "metric_description": f"{spec['aggregate']} of {spec['metric']} over the first {spec['window_days']} days", "videos": table,
            "summary": summary, "caveats": CAVEATS}
