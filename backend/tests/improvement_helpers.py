"""Shared set-up for the Phase 6 tests: finished workflows with recorded QA and originality histories."""

from __future__ import annotations

from app.enums import VideoStage
from app.models.production import OriginalityReview, QAReport
from app.services import videos
from tests.prod_helpers import BDAY, MED, STORY  # noqa: F401
from tests.test_gate_videos import make_ready_video


def check(cid, status="pass", name=None, detail="ok", correction="none needed"):
    return {"id": cid, "name": name or cid.replace("_", " "), "status": status, "detail": detail, "measured": None, "correction": correction}


def add_qa(db, video_id, version, *, failing=(), passing=("duration", "loudness")):
    checks = [check(c, "fail", detail=f"{c} was out of range", correction=f"re-render with a corrected {c}") for c in failing] + [check(c) for c in passing]
    row = QAReport(video_id=video_id, version=version, passed=not failing, checks=checks, created_by="test")
    db.add(row)
    db.flush()
    return row


def add_originality(db, video_id, version, *, status="PASS", findings=()):
    report = {"status": status, "findings": [{"severity": "revise", "code": c, "message": f"{c} was found.", "evidence": {}} for c in findings],
              "required_corrective_actions": [{"code": c, "action": f"rewrite the part that triggers {c}"} for c in findings]}
    row = OriginalityReview(video_id=video_id, version=version, status=status, report=report, created_by="test")
    db.add(row)
    db.flush()
    return row


def finished_video(db, agent, *, channel=BDAY, title="Garden Song", qa_failure="caption_sync", originality_finding=None, through_gate=True):
    """A video whose workflow is complete (cleared for publishing) and whose history shows a failed first QA and, optionally, a revised originality review."""
    if through_gate:
        v = make_ready_video(db, agent, channel=channel, title=title)
        videos.set_stage(db, agent("production-manager"), v.id, VideoStage.READY)  # cleared for the publishing queue: the workflow is complete
    else:  # only the stage and the QA/originality history matter to lesson mining; the other channels have extra publication rules of their own
        v = videos.create(db, agent("production-manager"), channel, title)
        v.stage = VideoStage.READY.value
    add_qa(db, v.id, 1, failing=(qa_failure,) if qa_failure else ())
    add_qa(db, v.id, 2, passing=("duration", "loudness", *((qa_failure,) if qa_failure else ())))  # every report lists every check; the corrected one now passes
    if originality_finding:
        add_originality(db, v.id, 1, status="NEEDS_REVISION", findings=(originality_finding,))
        add_originality(db, v.id, 2)
    db.commit()
    return v.id


# --- experiments ----------------------------------------------------------------------------------------------------------------

from datetime import UTC, date, datetime, timedelta  # noqa: E402

from app.models.community import Metric  # noqa: E402

PUBLISHED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)  # a video published at noon UTC on 1 October (also 1 October in Pacific time)
FIRST_DAY = date(2026, 10, 1)
TODAY = date(2026, 10, 20)  # well after every 7-day window has ended


def publish(db, video_id, when=PUBLISHED):
    from app.models.videos import Video
    v = db.get(Video, video_id)
    v.published_at, v.stage, v.youtube_video_id = when, "PUBLISHED", f"yt{video_id}"
    db.flush()
    return v


def add_metric(db, video_id, metric, day, value, *, source="youtube_analytics"):
    from app.models.videos import Video
    v = db.get(Video, video_id)
    db.add(Metric(scope="video", channel_id=v.channel_id, video_id=video_id, metric=metric, day=day, value=float(value), source=source, retrieved_at=datetime.now(UTC)))
    db.flush()


def add_week(db, video_id, per_day, *, metric="views", first=FIRST_DAY, days=7, skip=()):
    for i in range(days):
        if i not in skip:
            add_metric(db, video_id, metric, first + timedelta(days=i), per_day)
    db.flush()


def spec(**over):
    return {"metric": "views", "aggregate": "sum", "window_days": 7, **over}
