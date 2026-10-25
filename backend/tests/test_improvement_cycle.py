"""The daily cycle's learning phase and the improvement claims in the department report."""

from __future__ import annotations

from datetime import timedelta

from app.db import session_scope
from app.improvement import experiments as ex
from app.models.improvement import Experiment
from app.models.tasks import Task
from app.ops import cycle
from app.services import reports
from app.timeutil import utcnow
from app.worker.runner import Runner
from app.youtube.quota import PACIFIC
from tests.improvement_helpers import BDAY, add_week, finished_video, publish, spec
from tests.prod_helpers import make_video


def phase(db, name):
    with session_scope() as s:
        return {p["phase"]: p for p in cycle.run_cycle(s)["phases"]}[name]


def test_a_finished_workflow_queues_the_lesson_task_once_a_day_and_the_proposals_count_as_waiting_for_you(db, owner, agent):
    finished_video(db, agent)
    first = phase(db, "learning")
    assert first["status"] == "done" and first["mining_task"] and "queued to be examined" in first["summary"]
    assert phase(db, "learning")["mining_task"] == first["mining_task"]  # the same day: the same task, never a second
    assert Runner("t").run_once() == first["mining_task"]
    after = phase(db, "learning")
    assert after["lessons_by_status"] == {"OBSERVED": 1} and after["needs_a_person"] == 1 and after["mining_task"] is None
    assert "1 lesson(s) waiting for review" in after["summary"]
    with session_scope(write=False) as s:
        t = s.get(Task, first["mining_task"])
        assert t.assigned_agent.slug == "continuous-improvement-scientist" and t.result["proposed"] == 1


def test_an_experiment_with_enough_finished_videos_is_flagged_ready_but_never_analysed_by_itself(db, owner, agent):
    e = ex.create(db, owner, channel_slug=BDAY, title="Bright thumbnails", hypothesis="Brighter thumbnails get more first-week views.", change="Use the bright palette.",
                  metric=spec(), min_per_group=5)
    rows = [ex.enroll(db, owner, e.id, make_video(db, agent, BDAY, f"Cycle video {i} {'zxcvbnmasd'[i]}{i}").id) for i in range(10)]
    ex.start(db, owner, e.id)
    db.commit()
    assert phase(db, "learning")["ready_to_analyse"] == []  # nothing is public yet
    went_public = utcnow() - timedelta(days=30)  # the cycle runs on the real clock, so the week of figures sits 30 days back
    first_day = went_public.astimezone(PACIFIC).date()
    for r in rows:
        publish(db, r.video_id, went_public)
        add_week(db, r.video_id, 100 if r.arm == "control" else 200, first=first_day)
    db.commit()
    p = phase(db, "learning")
    assert [x["experiment"] for x in p["ready_to_analyse"]] == [e.public_id] and p["ready_to_analyse"][0]["finished"] == {"control": 5, "treatment": 5}
    with session_scope(write=False) as s:
        assert s.get(Experiment, e.id).analysis is None  # looking is the owner's act, because every look is counted
    assert p["needs_a_person"] >= 1


def test_the_improvement_claims_in_the_department_report_are_rechecked_by_the_ceo(db, owner, agent):
    lid = finished_video(db, agent)
    from app.improvement import lessons
    from app.services import org
    lessons.mine_video(db, org.actor_for_slug(db, "continuous-improvement-scientist"), lid)
    db.commit()
    end = utcnow() + timedelta(minutes=1)
    rep = reports.build_department_report(db, agent("analytics-manager"), "analytics-improvement", end - timedelta(days=1), end)
    claims = {c["text"]: c for c in rep.content["claims"]}
    waiting = next(c for t, c in claims.items() if "waiting for investigation, verification" in t)
    assert waiting["value"] == 1 and waiting["check"]["entity"] == "board_entries" and waiting["cls"] == "verified_fact"
    waiting["check"]["expected"] = 0  # a manager who under-reports what is waiting is contradicted
    assert reports.run_check(db, waiting["check"], agent("ceo")).status == "contradicted"
    assert any("experiment(s) are running" in t for t in claims)
