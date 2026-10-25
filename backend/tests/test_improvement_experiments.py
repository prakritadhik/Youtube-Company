"""Experiments: random assignment before publication, strict measurement, no peeking, honest analysis, and an owner's decision that reaches the board."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.improvement import experiments as ex
from app.improvement import measure
from app.improvement.experiments import ExperimentError
from app.models.board import BoardEntry
from app.models.improvement import Experiment, ExperimentVideo
from app.models.videos import Video
from app.security.permissions import PermissionDenied
from app.services import board, evidence, org, videos
from app.storage import resolve_data_path
from tests.improvement_helpers import BDAY, FIRST_DAY, PUBLISHED, STORY, TODAY, add_metric, add_week, publish, spec
from tests.prod_helpers import make_video

PLAN = dict(channel_slug=BDAY, title="Bright thumbnails", hypothesis="Brighter thumbnails get more first-week views.", change="Use the bright palette on the thumbnail.")
_n = [0]


def make(db, owner, **over):
    kw = {**PLAN, "metric": spec(), "min_per_group": 5, **over}
    return ex.create(db, owner, **kw)


def new_video(db, agent, title=None):
    _n[0] += 1
    return make_video(db, agent, BDAY, title or f"Video number {_n[0]} {'abcdefghij'[_n[0] % 10]}{_n[0]}").id


def enrolled(db, owner, agent, exp, n):
    return [ex.enroll(db, owner, exp.id, new_video(db, agent)) for _ in range(n)]


def finish(db, rows, control_views, treatment_views, *, per_day=True):
    """Publish every enrolled video and give it a complete first week; values are per-video totals spread evenly (or per day)."""
    c, t = iter(control_views), iter(treatment_views)
    for r in rows:
        total = next(c) if r.arm == "control" else next(t)
        publish(db, r.video_id)
        add_week(db, r.video_id, total if per_day else total / 7)
    db.flush()


# --- design -------------------------------------------------------------------------------------------------------------------


def test_an_experiment_needs_a_real_plan(db, owner):
    for bad in (dict(min_per_group=4), dict(min_per_group=51), dict(metric=spec(metric="revenue")), dict(metric=spec(window_days=0)), dict(metric=spec(window_days=91)),
                dict(metric=spec(aggregate="latest")), dict(metric=spec(source="youtube_data")), dict(hypothesis=" "), dict(change=""), dict(channel_slug="nope")):
        with pytest.raises(ExperimentError):
            make(db, owner, **bad)
    e = make(db, owner, confounders=["a school holiday in week two"])
    assert e.public_id.startswith("EXP-") and e.status == "DESIGN" and e.confounders[-1] == "a school holiday in week two" and len(e.confounders) == len(ex.DEFAULT_CONFOUNDERS) + 1
    assert e.metric == {"metric": "views", "source": "youtube_analytics", "aggregate": "sum", "window_days": 7, "higher_is_better": True}


# --- assignment ---------------------------------------------------------------------------------------------------------------


def test_arms_are_balanced_seeded_recorded_and_can_be_recomputed(db, owner, agent):
    e = make(db, owner)
    rows = enrolled(db, owner, agent, e, 10)
    arms = [r.arm for r in rows]
    assert arms.count("control") == arms.count("treatment") == 5
    for pair in zip(arms[::2], arms[1::2], strict=True):
        assert set(pair) == {"control", "treatment"}  # permuted blocks of two: every pair has one of each
    for r in rows:  # anyone can redo the draw from the seed
        again, draw = ex._draw(e, r.draw["counts_before"], r.video_id)
        assert again == r.arm and draw == r.draw and r.draw["seed"] == e.seed


def test_the_draw_is_random_not_a_fixed_alternation():
    class E:  # a stand-in with just a seed
        def __init__(self, seed):
            self.seed = seed
    firsts = [ex._draw(E(s), {"control": 0, "treatment": 0}, 7)[0] for s in range(40)]
    assert 8 <= firsts.count("treatment") <= 32


def test_assignment_rules(db, owner, agent):
    e, other = make(db, owner), make(db, owner, title="Another plan")
    v = new_video(db, agent)
    ex.enroll(db, owner, e.id, v)
    with pytest.raises(ExperimentError, match="already in"):
        ex.enroll(db, owner, other.id, v)  # one experiment per video: two changes never share one
    with pytest.raises(ExperimentError, match="already in"):
        ex.enroll(db, owner, e.id, v)  # and never twice in the same one: the arm, once drawn, is final
    published = new_video(db, agent)
    publish(db, published)
    with pytest.raises(ExperimentError, match="before publication"):
        ex.enroll(db, owner, e.id, published)
    private_upload = new_video(db, agent)
    db.get(Video, private_upload).youtube_video_id = "ytprivate"  # on YouTube but not public: the stage has not changed, yet the arm could now be chosen with hindsight
    with pytest.raises(ExperimentError, match="before publication"):
        ex.enroll(db, owner, e.id, private_upload)
    other_channel = videos.create(db, org.actor_for_slug(db, "production-manager"), STORY, "A story").id
    with pytest.raises(ExperimentError, match="channel"):
        ex.enroll(db, owner, e.id, other_channel)
    with pytest.raises(PermissionDenied):
        ex.enroll(db, org.actor_for_slug(db, "comment-analysis-specialist"), e.id, new_video(db, agent))


# --- starting: the board's real steps -----------------------------------------------------------------------------------------


def test_starting_registers_the_plan_on_the_board_through_its_real_checks(db, owner, agent):
    e = make(db, owner)
    ex.start(db, owner, e.id)
    entry = db.get(BoardEntry, e.board_entry_id)
    assert (e.status, entry.status, entry.category, entry.risk_class) == ("RUNNING", "APPROVED_FOR_TESTING", "experiment", "routine") and entry.channel_id == e.channel_id
    assert set(entry.test_plan) == {"hypothesis", "success_metric", "baseline", "observation_period"} and "unavailable" in entry.test_plan["baseline"]
    assert "This is a test, not a rule" in entry.description and "ONLY to videos assigned to the treatment arm" in entry.description
    kinds = [x.kind for x in evidence.for_board_entry(db, entry.id)]
    assert kinds == ["db_record"] and evidence.for_board_entry(db, entry.id)[0].verification_status == "verified"  # the CEO re-checked it
    with pytest.raises(ExperimentError):
        ex.start(db, owner, e.id)  # only once
    with pytest.raises(PermissionDenied):
        ex.start(db, org.actor_for_slug(db, "analytics-manager"), make(db, owner, title="Second").id)


def test_the_baseline_is_measured_from_the_channels_earlier_public_videos(db, owner, agent):
    earlier = [new_video(db, agent) for _ in range(3)]
    for i, vid in enumerate(earlier):
        publish(db, vid, PUBLISHED - timedelta(days=60 + i))
        add_week(db, vid, 10 * (i + 1), first=FIRST_DAY - timedelta(days=60 + i))
    e = make(db, owner)
    b = ex.baseline(db, e, today=TODAY)
    assert b["status"] == "available" and b["n"] == 3 and b["median"] == 140.0 and sorted(x["value"] for x in b["videos"]) == [70.0, 140.0, 210.0]


# --- measuring ----------------------------------------------------------------------------------------------------------------


def test_a_video_that_is_not_public_has_no_value_and_says_why(db, owner, agent):
    v = db.get(Video, new_video(db, agent))
    m = measure.measure_video(db, v, measure.check_spec(spec()), today=TODAY)
    assert m["status"] == "unavailable" and m["value"] is None and "not public" in m["reason"]


def test_a_window_that_has_not_ended_or_has_a_missing_day_is_incomplete_never_zero(db, owner, agent):
    vid = new_video(db, agent)
    publish(db, vid)
    add_week(db, vid, 10, skip=(3,))
    v = db.get(Video, vid)
    s = measure.check_spec(spec())
    early = measure.measure_video(db, v, s, today=FIRST_DAY + timedelta(days=7))  # the window ended on day 6; two days of lag are needed
    assert early["status"] == "incomplete" and "revises recent days" in early["reason"]
    gap = measure.measure_video(db, v, s, today=TODAY)
    assert gap["status"] == "incomplete" and gap["value"] is None and "1 of 7 days" in gap["reason"] and "never counted as zero" in gap["reason"]
    add_metric(db, vid, "views", FIRST_DAY + timedelta(days=3), 10)
    ok = measure.measure_video(db, v, s, today=TODAY)
    assert ok["status"] == "complete" and ok["value"] == 70.0 and ok["window"] == {"start": "2026-10-01", "end": "2026-10-07"}


def test_means_latest_readings_and_owner_entered_figures_are_read_from_the_right_source(db, owner, agent):
    vid = new_video(db, agent)
    publish(db, vid)
    add_week(db, vid, 50.0, metric="averageViewPercentage")
    for i in range(7):
        add_metric(db, vid, "views_total", FIRST_DAY + timedelta(days=i), 100 * (i + 1), source="youtube_data")
        add_metric(db, vid, "likes", FIRST_DAY + timedelta(days=i), 2, source="owner_entered")
    v = db.get(Video, vid)
    assert measure.measure_video(db, v, measure.check_spec(spec(metric="averageViewPercentage", aggregate="mean")), today=TODAY)["value"] == 50.0
    assert measure.measure_video(db, v, measure.check_spec(spec(metric="views_total", aggregate="latest")), today=TODAY)["value"] == 700.0
    assert measure.measure_video(db, v, measure.check_spec(spec(metric="likes", source="owner_entered")), today=TODAY)["value"] == 14.0
    assert measure.measure_video(db, v, measure.check_spec(spec(metric="likes")), today=TODAY)["status"] == "incomplete"  # entered figures are not mixed into retrieved ones


def test_a_scheduled_video_whose_time_has_passed_is_measured_but_says_it_is_not_confirmed_public(db, owner, agent):
    vid = new_video(db, agent)
    v = db.get(Video, vid)
    v.publish_at, v.stage = PUBLISHED, "SCHEDULED"
    add_week(db, vid, 10)
    m = measure.measure_video(db, v, measure.check_spec(spec()), today=TODAY)
    assert m["status"] == "complete" and "not confirmed public" in m["basis"]


# --- analysing ----------------------------------------------------------------------------------------------------------------


def running(db, owner, agent, n=10, **over):
    e = make(db, owner, **over)
    rows = enrolled(db, owner, agent, e, n)
    ex.start(db, owner, e.id)
    return e, rows


def test_no_test_is_run_before_the_planned_number_of_videos_has_finished(db, owner, agent):
    e, rows = running(db, owner, agent, 10)
    finish(db, rows[:8], [100] * 4, [900] * 4)  # only four per arm have complete figures; the other two are not even public
    a = ex.analyse(db, owner, e.id, today=TODAY)
    assert a["conclusion"] == "insufficient_data" and a["p_value"] is None and a["looks"] == 0 and a["suggested_decision"] == "repeat"
    assert {v["status"] for v in a["videos"]} == {"complete", "unavailable"} and a["attrition"]["control"] == {"enrolled": 5, "complete": 4}


def test_a_clear_difference_is_supported_with_its_limits_and_leaves_checkable_evidence_on_the_board(db, owner, agent):
    e, rows = running(db, owner, agent, 10)
    finish(db, rows, [100, 110, 90, 105, 95], [200, 210, 190, 205, 195])
    a = ex.analyse(db, owner, e.id, today=TODAY)
    assert a["conclusion"] == "supported_improvement" and abs(a["p_value"] - 2 / 252) < 1e-12 and a["confidence"] == "medium" and a["suggested_decision"] == "adopt"
    assert a["difference"] == 700.0 and a["looks"] == 1  # per-day values of 100 and 200 over seven days and a["control"]["n"] == a["treatment"]["n"] == 5
    assert any("single result" in x for x in a["limitations"]) and any("publication" in c for c in a["confounders"])
    ev = evidence.for_board_entry(db, e.board_entry_id)
    assert [x.kind for x in ev] == ["db_record", "file", "db_record"]
    evidence.verify_all(db, ev, org.actor_for_slug(db, "ceo"))
    assert {x.verification_status for x in ev} == {"verified"}
    doc = json.loads(resolve_data_path(a["artifact"]).read_text())
    assert doc["experiment"] == e.public_id and len(doc["videos"]) == 10  # the saved analysis lists every video and its value


def test_no_clear_difference_is_not_adopted_and_repeated_looks_are_counted(db, owner, agent):
    e, rows = running(db, owner, agent, 10)
    finish(db, rows, [100, 120, 90, 110, 95], [105, 115, 92, 108, 99])
    a1 = ex.analyse(db, owner, e.id, today=TODAY)
    a2 = ex.analyse(db, owner, e.id, today=TODAY)
    assert a1["conclusion"] == "no_clear_difference" and a1["looks"] == 1 and a2["looks"] == 2 and any("look number 2" in x for x in a2["limitations"])


def test_videos_lost_from_one_arm_are_named_not_dropped_silently(db, owner, agent):
    e, rows = running(db, owner, agent, 12)
    ctl = [r for r in rows if r.arm == "control"]
    tre = [r for r in rows if r.arm == "treatment"]
    finish(db, ctl + tre[:5], [100, 110, 90, 105, 95, 99], [200, 210, 190, 205, 195])
    a = ex.analyse(db, owner, e.id, today=TODAY)
    assert a["attrition"] == {"control": {"enrolled": 6, "complete": 6}, "treatment": {"enrolled": 6, "complete": 5}}
    assert any("left out" in x and "biased" in x for x in a["limitations"]) and [v["arm"] for v in a["videos"] if v["status"] != "complete"] == ["treatment"]


def test_lower_is_better_flips_the_direction(db, owner, agent):
    e, rows = running(db, owner, agent, 10, metric=spec(higher_is_better=False))
    finish(db, rows, [200, 210, 190, 205, 195], [100, 110, 90, 105, 95])
    assert ex.analyse(db, owner, e.id, today=TODAY)["conclusion"] == "supported_improvement"


# --- deciding -----------------------------------------------------------------------------------------------------------------


def supported(db, owner, agent):
    e, rows = running(db, owner, agent, 10)
    finish(db, rows, [100, 110, 90, 105, 95], [200, 210, 190, 205, 195])
    ex.analyse(db, owner, e.id, today=TODAY)
    return e


def test_only_the_owner_decides_and_only_on_a_recorded_analysis(db, owner, agent):
    e, _ = running(db, owner, agent, 10)
    with pytest.raises(ExperimentError, match="analyse"):
        ex.decide(db, owner, e.id, "reject", "no analysis yet")
    e2 = supported(db, owner, agent)
    with pytest.raises(PermissionDenied):
        ex.decide(db, org.actor_for_slug(db, "ceo"), e2.id, "adopt", "the CEO may not adopt on the owner's behalf")
    with pytest.raises(ExperimentError, match="one of"):
        ex.decide(db, owner, e2.id, "maybe", "x")
    with pytest.raises(ExperimentError, match="why"):
        ex.decide(db, owner, e2.id, "adopt", "  ")


def test_adopting_makes_a_medium_confidence_instruction_that_later_tasks_reference(db, owner, agent):
    e = supported(db, owner, agent)
    version = board.current_release_version(db)
    out = ex.decide(db, owner, e.id, "adopt", "ten videos, a clear gap, and it is cheap to do")
    entry = db.get(BoardEntry, e.board_entry_id)
    assert out["experiment"]["status"] == "CONCLUDED" and out["experiment"]["decision"] == "adopt" and (entry.status, entry.confidence) == ("ACTIVE_INSTRUCTION", "medium")
    assert "Result: The treatment videos were better" in entry.description and "single result" in entry.description
    assert board.current_release_version(db) > version
    db.commit()
    from tests.test_improvement_lessons import run_probe
    task, seen = run_probe(db, "final-qa-specialist", BDAY)
    assert entry.public_id in task.lessons_applied and entry.public_id in seen["lessons"]


def test_an_unsupported_result_cannot_be_adopted(db, owner, agent):
    e, rows = running(db, owner, agent, 10)
    finish(db, rows, [100, 120, 90, 110, 95], [105, 115, 92, 108, 99])
    ex.analyse(db, owner, e.id, today=TODAY)
    with pytest.raises(ExperimentError, match="does not support adopting"):
        ex.decide(db, owner, e.id, "adopt", "I want it anyway")
    assert db.get(BoardEntry, e.board_entry_id).status == "APPROVED_FOR_TESTING"


def test_a_harmful_result_is_rejected_and_recorded_as_a_failed_experiment(db, owner, agent):
    e, rows = running(db, owner, agent, 10)
    finish(db, rows, [200, 210, 190, 205, 195], [100, 110, 90, 105, 95])
    assert ex.analyse(db, owner, e.id, today=TODAY)["conclusion"] == "supported_harm"
    ex.decide(db, owner, e.id, "reject", "it made things worse")
    entry = db.get(BoardEntry, e.board_entry_id)
    assert entry.status == "REJECTED" and "worse" in entry.status_reason and e.status == "CONCLUDED"


def test_repeat_expires_the_old_entry_and_opens_a_new_plan_with_a_new_seed(db, owner, agent):
    e, rows = running(db, owner, agent, 10)
    finish(db, rows, [100, 120, 90, 110, 95], [105, 115, 92, 108, 99])
    ex.analyse(db, owner, e.id, today=TODAY)
    out = ex.decide(db, owner, e.id, "repeat", "inconclusive; run it again with fresh videos")
    child = db.get(Experiment, out["repeat"]["id"])
    assert db.get(BoardEntry, e.board_entry_id).status == "EXPIRED" and child.status == "DESIGN" and child.parent_id == e.id and child.seed != e.seed and child.metric == e.metric
    assert db.query(ExperimentVideo).filter_by(experiment_id=child.id).count() == 0  # fresh videos, fresh draw


def test_revise_expires_without_opening_anything_and_cancel_closes_a_plan(db, owner, agent):
    e, rows = running(db, owner, agent, 10)
    finish(db, rows, [100, 120, 90, 110, 95], [105, 115, 92, 108, 99])
    ex.analyse(db, owner, e.id, today=TODAY)
    assert ex.decide(db, owner, e.id, "revise", "the change was too small to matter")["repeat"] is None
    draft = make(db, owner, title="Never started")
    assert ex.cancel(db, owner, draft.id, "no longer wanted").status == "CANCELLED"
    live, _ = running(db, owner, agent, 6, title="Cancelled mid-way")
    ex.cancel(db, owner, live.id, "the channel changed direction")
    assert db.get(BoardEntry, live.board_entry_id).status == "EXPIRED"
    with pytest.raises(ExperimentError):
        ex.enroll(db, owner, live.id, new_video(db, agent))  # a closed experiment takes no more videos


# --- what the person making the video sees ------------------------------------------------------------------------------------------


def test_the_person_making_a_video_is_told_which_arm_it_is_in_and_what_to_do(db, owner, agent):
    e = make(db, owner)
    rows = enrolled(db, owner, agent, e, 2)
    seen = {r.arm: ex.for_video(db, r.video_id)[0] for r in rows}
    assert seen["treatment"]["instruction"].startswith("Apply this change to this video: Use the bright palette")
    assert "control video" in seen["control"]["instruction"] and "do NOT apply" in seen["control"]["instruction"]
    assert ex.for_video(db, new_video(db, agent)) == []


def test_every_experiment_lesson_is_read_per_channel(db, owner, agent):
    e, _ = running(db, owner, agent, 2)
    assert [x["public_id"] for x in board.instructions_for(db, BDAY, "production")["experiments"]] == [db.get(BoardEntry, e.board_entry_id).public_id]
    assert board.instructions_for(db, STORY, "production")["experiments"] == []
    assert db.scalars(select(BoardEntry)).first().channel_id == e.channel_id
