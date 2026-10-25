"""Once a channel is connected the reports and the daily cycle use real counts, and say what is still unavailable."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.analytics import collect
from app.community import collect as comments_collect
from app.community import replies
from app.db import session_scope
from app.models.community import Comment
from app.models.reports import Report
from app.ops import cycle
from app.security.permissions import Actor
from app.services import reports
from app.services import tasks as tasks_svc
from app.timeutil import utcnow
from app.worker.runner import HANDLERS, Runner
from tests.analytics_helpers import connect, daily_rows, thread, uploaded
from tests.google_fixtures import google  # noqa: F401


def window():
    end = utcnow() + timedelta(minutes=1)
    return end - timedelta(days=1), end


def phases(out):
    return {p["phase"]: p for p in out["phases"]}


def test_the_cycle_says_when_a_connected_channel_has_not_been_collected_yet(db, owner, agent, google):
    connect(db, owner, google)
    with session_scope() as s:
        p = phases(cycle.run_cycle(s))["monitoring"]
    assert p["status"] == "unavailable" and "Nothing has been collected yet" in p["summary"] and p["channels_connected"] == 1


def test_the_cycle_reports_real_collection_results_and_asks_for_a_person_when_comments_need_one(db, owner, agent, google):
    connect(db, owner, google)
    uploaded(db, agent, google)
    google.analytics_days["vid001"] = daily_rows()
    google.threads["vid001"] = [thread("c1", "Love this song!"), thread("c2", "I want to hurt myself")]
    system = Actor.system("test")
    for tt in ("analytics.collect", "community.collect_comments"):  # the scheduled tasks, run by the real worker
        tasks_svc.create(db, system, title=tt, task_type=tt, review_required=False, idempotency_key=f"cycle-{tt}")
        db.commit()
        assert Runner("t", HANDLERS).run_once() is not None
    with session_scope() as s:
        out = cycle.run_cycle(s)
    p = phases(out)["monitoring"]
    assert p["status"] == "done" and p["figures_on_record"] > 10 and p["comments_waiting"] == {"low": 1, "escalate": 1} and "1 of them escalated" in p["summary"]
    assert p["needs_a_person"] == 1 and out["needs_attention"] >= 1 and out["quiet"] is False


def test_open_reply_drafts_count_as_waiting_for_the_owner(db, owner, agent, google):
    connect(db, owner, google)
    vid = uploaded(db, agent, google)
    google.threads["vid001"] = [thread("c1", "Love this song!")]
    comments_collect.collect_video(vid)
    replies.create_draft(db, owner, db.scalar(select(Comment.id)))
    db.commit()
    with session_scope() as s:
        p = phases(cycle.run_cycle(s))["monitoring"]
    assert p["reply_drafts_waiting"] == 1 and p["needs_a_person"] == 1


def test_department_reports_replace_the_not_connected_claim_with_real_checkable_counts(db, owner, agent, google):
    connect(db, owner, google)
    vid = uploaded(db, agent, google)
    google.analytics_days["vid001"] = daily_rows()
    google.threads["vid001"] = [thread("c1", "Love this song!"), thread("c2", "Can I take 500 mg?")]
    collect.collect_video(vid)
    comments_collect.collect_video(vid)
    s, e = window()
    ex = reports.run_reporting_cycle(db, s, e)
    claims = {c["text"]: c for r in db.scalars(select(Report).where(Report.kind == "department")) for c in r.content["claims"]}
    assert not [t for t in claims if "YouTube is not connected" in t]
    assert any(c["cls"] == "unavailable" and "Revenue" in c["text"] for c in claims.values())
    conn = next(c for t, c in claims.items() if "channel(s) are connected to YouTube" in t)
    assert conn["value"] == 1 and conn["check"]["entity"] == "youtube_connections" and conn["cls"] == "verified_fact"
    figs = next(c for t, c in claims.items() if "performance figure(s) were retrieved" in t)
    assert figs["value"] > 10 and figs["check"]["entity"] == "metrics"
    esc = next(c for t, c in claims.items() if "escalated for a person" in t)
    assert esc["value"] == 1
    # the CEO re-ran every check against the database and none was contradicted
    assert ex.content["check_summary"].get("contradicted", 0) == 0
    texts = " ".join(u["statement"] for u in ex.content["sections"]["unavailable_data"])
    assert "Revenue" in texts and "YouTube is not connected" not in texts


def test_an_inflated_analytics_claim_is_caught_by_the_ceo(db, owner, agent, google):
    connect(db, owner, google)
    s, e = window()
    rep = reports.build_department_report(db, agent("analytics-manager"), "analytics-improvement", s, e)
    claim = next(c for c in rep.content["claims"] if "performance figure(s)" in c["text"])
    claim["check"]["expected"] = 500  # the manager claims five hundred figures were retrieved; the database has none
    result = reports.run_check(db, claim["check"], agent("ceo"))
    assert result.status == "contradicted" and result.actual == 0
