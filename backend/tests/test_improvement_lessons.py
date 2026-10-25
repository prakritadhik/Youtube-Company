"""Lessons mined from completed workflows: real evidence, one lesson per finding and channel, and a path from proposal to a later task."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.enums import VideoStage
from app.improvement import board_actions, lessons
from app.improvement.lessons import LessonError
from app.models.board import BoardAcknowledgement, BoardEntry
from app.models.improvement import WorkflowLesson
from app.models.tasks import Task
from app.security.permissions import PermissionDenied
from app.services import board, evidence, org, tasks, videos
from app.worker.runner import HandlerResult, Runner
from tests.improvement_helpers import BDAY, MED, STORY, add_qa, finished_video
from tests.test_gate_videos import make_ready_video


def scientist(db):
    return org.actor_for_slug(db, "continuous-improvement-scientist")


def entry(db, public_id):
    return db.scalar(select(BoardEntry).where(BoardEntry.public_id == public_id))


def test_a_corrected_qa_failure_becomes_an_observed_low_confidence_lesson_with_checkable_evidence(db, owner, agent):
    vid = finished_video(db, agent)
    out = lessons.mine_video(db, scientist(db), vid)
    assert [x["action"] for x in out["lessons"]] == ["proposed"]
    e = entry(db, out["lessons"][0]["lesson"])
    assert (e.status, e.confidence, e.risk_class, e.category) == ("OBSERVED", "low", "policy", "quality") and e.channel_id is not None
    assert "caption sync" in e.title and "re-render with a corrected caption_sync" in e.description and "One occurrence is an observation" in e.description
    ev = evidence.for_board_entry(db, e.id)
    assert {x.ref.split(":")[0] for x in ev} == {"qa_reports"} and len(ev) == 2 and all(x.kind == "db_record" for x in ev)
    evidence.verify_all(db, ev, org.actor_for_slug(db, "ceo"))
    assert {x.verification_status for x in ev} == {"verified"}  # the CEO's re-check is real: the records exist


def test_a_resolved_originality_finding_becomes_a_lesson_with_its_corrective_action(db, agent):
    vid = finished_video(db, agent, qa_failure=None, originality_finding="REPEATS_EARLIER_UPLOAD")
    out = lessons.mine_video(db, scientist(db), vid)
    assert [x["kind"] for x in out["lessons"]] == ["originality"]
    e = entry(db, out["lessons"][0]["lesson"])
    assert e.category == "originality" and "REPEATS_EARLIER_UPLOAD" in e.title and "rewrite the part that triggers REPEATS_EARLIER_UPLOAD" in e.description
    assert {x.ref.split(":")[0] for x in evidence.for_board_entry(db, e.id)} == {"originality_reviews"}


def test_a_workflow_that_never_failed_teaches_nothing_and_is_not_examined_twice(db, agent):
    vid = finished_video(db, agent, qa_failure=None)
    out = lessons.mine_video(db, scientist(db), vid)
    assert out["lessons"] == [] and not out["already_examined"]
    assert lessons.mine_video(db, scientist(db), vid)["already_examined"] is True
    assert db.query(BoardEntry).count() == 0


def test_only_a_completed_workflow_is_mined_and_a_failed_final_report_proves_nothing(db, agent):
    pm = agent("production-manager")
    draft = videos.create(db, pm, BDAY, "Not done")
    with pytest.raises(LessonError):
        lessons.mine_video(db, scientist(db), draft.id)
    v = make_ready_video(db, agent, title="Still failing")
    videos.set_stage(db, pm, v.id, VideoStage.READY)
    add_qa(db, v.id, 1, failing=("caption_sync",))
    add_qa(db, v.id, 2, failing=("caption_sync",))  # never corrected
    assert lessons.mine_video(db, scientist(db), v.id)["lessons"] == []


def test_the_same_finding_on_another_video_of_the_channel_adds_evidence_not_a_second_lesson(db, agent):
    first = finished_video(db, agent, title="Garden Song")
    second = finished_video(db, agent, title="Rainy Lullaby")
    a = lessons.mine_video(db, scientist(db), first)["lessons"][0]
    b = lessons.mine_video(db, scientist(db), second)["lessons"][0]
    assert a["lesson"] == b["lesson"] and (a["action"], b["action"]) == ("proposed", "evidence_added")
    assert db.query(BoardEntry).count() == 1 and len(evidence.for_board_entry(db, entry(db, a["lesson"]).id)) == 4


def test_a_lesson_never_crosses_channels(db, agent):
    a = lessons.mine_video(db, scientist(db), finished_video(db, agent, title="Garden Song"))["lessons"][0]["lesson"]
    b = lessons.mine_video(db, scientist(db), finished_video(db, agent, channel=STORY, title="The Lost Lantern", through_gate=False))["lessons"][0]["lesson"]
    assert a != b and entry(db, a).channel_id != entry(db, b).channel_id


def test_a_rejected_lesson_is_not_reopened_by_a_later_video(db, owner, agent):
    a = lessons.mine_video(db, scientist(db), finished_video(db, agent, title="Garden Song"))["lessons"][0]["lesson"]
    board_actions.act(db, owner, a, "reject", "the owner does not want this as a rule")
    again = lessons.mine_video(db, scientist(db), finished_video(db, agent, title="Rainy Lullaby"))["lessons"][0]
    assert again["action"] == "skipped_closed" and entry(db, a).status == "REJECTED"


def test_candidates_are_the_unexamined_completed_workflows(db, agent):
    a, b = finished_video(db, agent, title="Garden Song"), finished_video(db, agent, title="Moonlight Dance")
    assert lessons.candidates(db) == [a, b]
    lessons.mine_video(db, scientist(db), a)
    assert lessons.candidates(db) == [b]


# --- the acceptance criterion: approved, stored, communicated to relevant workers, referenced in a later task --------------------------


def run_probe(db, slug, channel):
    """Create a task for a role and run it through the real worker; returns the task row and what the handler saw."""
    seen = {}

    def probe(ctx):
        seen["lessons"] = list(ctx.lessons)
        return HandlerResult(result={"ok": True})

    t = tasks.create(db, org.actor_for_slug(db, "ceo"), title=f"probe {slug}", task_type="test.probe", assigned_agent_slug=slug, channel_slug=channel,
                     idempotency_key=f"probe-{slug}-{channel}-{db.query(Task).count()}")
    db.commit()
    assert Runner("t", {"test.probe": probe}).run_once() == t.id
    with session_scope(write=False) as s:
        row = s.get(Task, t.id)
        s.expunge(row)
    return row, seen


def test_a_verified_lesson_is_approved_stored_sent_to_relevant_workers_and_referenced_in_a_later_task(db, owner, agent):
    vid = finished_video(db, agent)
    lid = lessons.mine_video(db, scientist(db), vid)["lessons"][0]["lesson"]
    db.commit()

    # before approval nothing is an instruction: a task started now references nothing
    early, seen = run_probe(db, "final-qa-specialist", BDAY)
    assert early.lessons_applied == [] and seen["lessons"] == []

    # an unverified observation cannot be activated, whoever asks
    with pytest.raises(board.BoardError):
        board_actions.act(db, owner, lid, "activate")
    board_actions.act(db, owner, lid, "investigate", "checking the QA records")
    v = board_actions.act(db, owner, lid, "verify")
    assert v["performed_by"].endswith("ceo") and v["entry"]["status"] == "VERIFIED"  # the CEO's own check, run on the owner's request
    assert v["entry"]["evidence"] and {e["verification_status"] for e in v["entry"]["evidence"]} == {"verified"}
    version_before = board.current_release_version(db)
    done = board_actions.act(db, owner, lid, "activate")
    assert done["entry"]["status"] == "ACTIVE_INSTRUCTION" and done["approval_id"] is None  # the owner is the approver
    assert board.current_release_version(db) == version_before + 1  # stored and published as a new board version
    db.commit()

    # communicated: a relevant worker (production) on this channel must acknowledge and records the lesson on its next task
    later, seen = run_probe(db, "final-qa-specialist", BDAY)
    assert later.lessons_applied == [lid] and seen["lessons"] == [lid] and later.result["lessons_applied"] == [lid]
    assert later.board_version_used == version_before + 1
    with session_scope(write=False) as s:
        acks = s.scalars(select(BoardAcknowledgement)).all()
        assert any(a.task_id == later.id for a in acks)

    # not communicated to workers it does not concern: another channel, and a department the category is not for
    other_channel, _ = run_probe(db, "final-qa-specialist", MED)
    assert other_channel.lessons_applied == []
    other_dept, _ = run_probe(db, "comment-analysis-specialist", BDAY)
    assert other_dept.lessons_applied == []


def test_verification_fails_when_the_evidence_is_gone(db, owner, agent):
    lid = lessons.mine_video(db, scientist(db), finished_video(db, agent))["lessons"][0]["lesson"]
    board_actions.act(db, owner, lid, "investigate", "checking")
    from app.models.production import QAReport
    db.query(QAReport).delete()  # the records the lesson cites no longer exist
    db.flush()
    with pytest.raises(board.BoardError) as e:
        board_actions.act(db, owner, lid, "verify")
    assert "evidence failed verification" in str(e.value)
    assert entry(db, lid).status == "UNDER_INVESTIGATION"


def test_only_the_owner_or_ceo_can_ask_for_the_ceo_step_and_a_viewer_cannot_activate(db, owner, viewer, agent):
    lid = lessons.mine_video(db, scientist(db), finished_video(db, agent))["lessons"][0]["lesson"]
    board_actions.act(db, owner, lid, "investigate", "checking")
    with pytest.raises(PermissionDenied):
        board_actions.act(db, org.actor_for_slug(db, "production-manager"), lid, "verify")
    board_actions.act(db, owner, lid, "verify")
    with pytest.raises(PermissionDenied):
        board_actions.act(db, viewer, lid, "activate")


def test_a_lesson_whose_text_is_edited_after_activation_stops_applying_until_it_is_verified_again(db, owner, agent):
    lid = lessons.mine_video(db, scientist(db), finished_video(db, agent))["lessons"][0]["lesson"]
    for a in ("investigate", "verify", "activate"):
        board_actions.act(db, owner, lid, a, "x")
    board.revise(db, org.actor_for_slug(db, "analytics-manager"), entry(db, lid).id, description="quietly changed wording")
    db.commit()
    later, _ = run_probe(db, "final-qa-specialist", BDAY)
    assert later.lessons_applied == [] and entry(db, lid).status == "UNDER_INVESTIGATION"


def test_the_mining_task_runs_as_the_scientist_and_leaves_checkable_evidence(db, owner, agent):
    finished_video(db, agent)
    t = tasks.create(db, org.actor_for_slug(db, "analytics-manager"), title="Mine lessons", task_type="improvement.mine_lessons",
                     assigned_agent_slug="continuous-improvement-scientist", idempotency_key="mine-1")
    db.commit()
    assert Runner("t").run_once() == t.id
    with session_scope(write=False) as s:
        row = s.get(Task, t.id)
        assert row.status == "SUBMITTED" and row.result["videos_examined"] == 1 and row.result["proposed"] == 1
        kinds = {e.kind for e in evidence.for_task(s, t.id)}
        assert kinds == {"file", "db_record"}
        assert s.query(WorkflowLesson).filter_by(kind="examined").count() == 1
