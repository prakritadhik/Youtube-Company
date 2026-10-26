"""The conductor: it moves a conducted video through the steps no worker covers, and stops wherever a person has to decide."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai import autopilot, conductor
from app.db import session_scope
from app.enums import ApprovalKind
from app.enums import TaskStatus as S
from app.models.approvals import ApprovalRequest
from app.models.research import ContentIdea
from app.models.tasks import Task
from app.models.videos import Video
from app.production import originality, qa
from app.research import ideas
from app.services import settings as settings_svc
from app.worker.runner import HANDLERS, Runner
from tests.ai_helpers import SPEC, approved_idea, brief_json, connect_brain, lyrics_json, review_json, run_company, say
from tests.prod_helpers import HAVE_FFMPEG

needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg/ffprobe not installed")


def go(db, owner, tmp_path, monkeypatch, *replies):
    fake = connect_brain(db, owner, tmp_path, monkeypatch)
    say(fake, *replies)
    out = conductor.start_with_ai(db, owner, approved_idea(db, owner).id, spec=SPEC)
    db.commit()
    return fake, out["video_id"]


def video(vid):
    with session_scope(write=False) as s:
        v = s.get(Video, vid)
        return v.stage, v.qa_passed, v.originality_status, v.ceo_rights_verified, v.owner_approval_status, conductor.progress(s, v)


def approvals_for(vid, kind):
    with session_scope(write=False) as s:
        return [a.status for a in s.scalars(select(ApprovalRequest).where(ApprovalRequest.kind == kind.value, ApprovalRequest.subject_id == vid))]


# --- starting --------------------------------------------------------------------------------------------------------------


def test_nothing_is_started_while_the_brain_cannot_be_used_and_the_reason_is_given(db, owner, tmp_path, monkeypatch):
    connect_brain(db, owner, tmp_path, monkeypatch)
    idea = approved_idea(db, owner)
    settings_svc.set_value(db, owner, "provider_attestations", {})  # the owner withdraws the confirmation after approving the idea
    db.commit()
    with pytest.raises(conductor.ConductorError, match="not ready.*confirmed"):
        conductor.start_with_ai(db, owner, idea.id)
    with session_scope(write=False) as s:
        assert s.get(ContentIdea, idea.id).status == "APPROVED" and s.scalar(select(Video)) is None  # nothing was created


def test_only_an_approved_idea_can_be_started(db, owner, tmp_path, monkeypatch):
    connect_brain(db, owner, tmp_path, monkeypatch)
    idea = ideas.propose(db, owner, "happy-birthday-songs", title="A song nobody approved", concept="An idea that was never approved by anyone at all.", audience="Children")
    db.commit()
    with pytest.raises(ideas.IdeaError, match="approved"):
        conductor.start_with_ai(db, owner, idea.id)


def test_a_started_video_is_marked_as_conducted_with_ai_drafting_tasks(db, owner, tmp_path, monkeypatch):
    fake, vid = go(db, owner, tmp_path, monkeypatch)
    with session_scope(write=False) as s:
        v = s.get(Video, vid)
        types = {t.task_type for t in s.scalars(select(Task).where(Task.video_id == vid))}
        assert v.metadata_json["conducted"] is True and v.stage == "RESEARCH"
        assert {"ai.draft_brief", "ai.draft_script", "seo.prepare_metadata", "production.render_video"} <= types and "manual.brief" not in types and "manual.script" not in types


# --- parked work -----------------------------------------------------------------------------------------------------------


def test_work_parked_because_the_brain_was_unavailable_resumes_by_itself_without_using_up_attempts(db, owner, tmp_path, monkeypatch):
    fake, vid = go(db, owner, tmp_path, monkeypatch)
    fake.set(error="result", message="Please run /login")
    r = Runner("w", HANDLERS)
    brief_task = r.run_once()
    with session_scope(write=False) as s:
        t = s.get(Task, brief_task)
        assert t.status == S.BLOCKED.value and t.blocked_reason.startswith("AI_NOT_SIGNED_IN") and t.attempts == 1
        assert conductor.progress(s, s.get(Video, vid))["step"] == "brain_unavailable"
    assert autopilot.step("w")["resumed"] == []  # still cooling down: it does not retry in a loop
    say(fake, brief_json())
    with session_scope() as s:  # the owner signs in and runs the connection test in Settings, which records a success
        from app.models.ai import AiGeneration

        s.add(AiGeneration(purpose="doctor", role_slug="owner", attempt=1, status="OK", prompt_sha256="0" * 64, prompt_chars=1))
    assert autopilot.step("w")["resumed"] == [brief_task]
    with session_scope(write=False) as s:
        t = s.get(Task, brief_task)
        assert t.status == S.QUEUED.value and t.attempts == 0 and t.blocked_reason is None
    assert r.run_once() == brief_task


# --- the whole way, and where it stops -------------------------------------------------------------------------------------


def run_to_the_end(db, owner, tmp_path, monkeypatch, patch=None):
    brief = brief_json()
    fake, vid = go(db, owner, tmp_path, monkeypatch, brief, review_json(), lyrics_json(brief), review_json())
    if patch:
        patch()
    run_company(Runner("w", HANDLERS))
    return fake, vid


@needs_ffmpeg
def test_publication_approval_is_requested_once_and_never_granted_by_the_company(db, owner, tmp_path, monkeypatch):
    fake, vid = run_to_the_end(db, owner, tmp_path, monkeypatch)
    assert video(vid)[:5] == ("READY", True, "PASS", True, "PENDING")
    assert approvals_for(vid, ApprovalKind.VIDEO_PUBLICATION) == ["PENDING"]
    for _ in range(3):
        autopilot.step("w")
    assert approvals_for(vid, ApprovalKind.VIDEO_PUBLICATION) == ["PENDING"] and approvals_for(vid, ApprovalKind.MEDICAL_REVIEW) == []
    # the owner says no: the company does not ask again
    with session_scope() as s:
        from app.services import approvals

        req = s.scalar(select(ApprovalRequest).where(ApprovalRequest.subject_id == vid, ApprovalRequest.kind == ApprovalKind.VIDEO_PUBLICATION.value))
        approvals.decide(s, owner, req.id, approve=False, note="not this one")
    for _ in range(3):
        autopilot.step("w")
    assert approvals_for(vid, ApprovalKind.VIDEO_PUBLICATION) == ["REJECTED"] and video(vid)[4] == "REJECTED"


@needs_ffmpeg
def test_a_failed_quality_check_opens_corrections_and_the_company_stops_there(db, owner, tmp_path, monkeypatch):
    real = qa.run_checks

    def failing(*a, **kw):
        checks = real(*a, **kw)
        checks[0] = {**checks[0], "status": "fail", "detail": "forced for the test", "correction": "re-render"}
        return checks

    fake, vid = run_to_the_end(db, owner, tmp_path, monkeypatch, patch=lambda: monkeypatch.setattr(qa, "run_checks", failing))
    stage, qa_ok, orig, ceo, owner_state, prog = video(vid)
    assert (qa_ok, orig, ceo, owner_state) == (False, "NOT_REVIEWED", False, "NOT_REQUESTED") and stage != "READY"
    assert prog["step"] == "corrections" and prog["who"] == "you"
    with session_scope(write=False) as s:
        assert any(t.task_type == "manual.correction" for t in s.scalars(select(Task).where(Task.video_id == vid)))
    assert approvals_for(vid, ApprovalKind.VIDEO_PUBLICATION) == []


@needs_ffmpeg
def test_a_rights_concern_waits_for_a_person_and_nothing_downstream_happens(db, owner, tmp_path, monkeypatch):
    real = originality.analyse

    def concerned(db_, video_):
        r = real(db_, video_)
        return {**r, "status": "HUMAN_REVIEW_REQUIRED", "unresolved_risks": ["forced for the test"]}

    fake, vid = run_to_the_end(db, owner, tmp_path, monkeypatch, patch=lambda: monkeypatch.setattr(originality, "analyse", concerned))
    stage, qa_ok, orig, ceo, owner_state, prog = video(vid)
    assert (qa_ok, orig, ceo, owner_state) == (True, "HUMAN_REVIEW_REQUIRED", False, "NOT_REQUESTED") and stage == "PACKAGING"
    assert prog["step"] == "rights_decision" and prog["who"] == "you"
    assert approvals_for(vid, ApprovalKind.VIDEO_PUBLICATION) == []


@needs_ffmpeg
def test_a_ceo_recheck_that_disagrees_stops_the_video_and_says_why(db, owner, tmp_path, monkeypatch):
    real = originality.analyse
    calls = {"n": 0}

    def flaky(db_, video_):
        calls["n"] += 1
        r = real(db_, video_)
        return r if calls["n"] == 1 else {**r, "status": "NEEDS_REVISION", "unresolved_risks": ["the CEO saw something the reviewer did not"]}

    fake, vid = run_to_the_end(db, owner, tmp_path, monkeypatch, patch=lambda: monkeypatch.setattr(originality, "analyse", flaky))
    stage, qa_ok, orig, ceo, owner_state, prog = video(vid)
    assert (orig, ceo, owner_state, stage) == ("PASS", False, "NOT_REQUESTED", "PACKAGING")
    assert prog["step"] == "stopped" and "CEO" in prog["detail"] and "something the reviewer did not" in prog["detail"]


@needs_ffmpeg
def test_videos_nobody_started_with_the_company_are_left_alone(db, owner, agent, tmp_path, monkeypatch):
    from app.production import pipeline
    from app.services import videos
    from tests.test_ai_review import SPEC as _  # noqa: F401

    connect_brain(db, owner, tmp_path, monkeypatch)
    pm = agent("production-manager")
    v = videos.create(db, pm, "happy-birthday-songs", "By hand")
    pipeline.start_production(db, pm, v.id, title="By hand", description="x", spec=SPEC)
    db.commit()
    assert conductor.conduct_all() == [] and video(v.id)[5] == {"conducted": False}


@needs_ffmpeg
def test_a_medicine_video_goes_to_a_human_medical_review_and_never_to_publication_approval_on_its_own(db, owner, agent, tmp_path, monkeypatch):
    from tests.test_ai_drafting import medicine_video

    brief = brief_json(source_requirements="Only the two recorded sources; no dosing.", safety_notes="Not personal advice; a person reviews before release.")
    fake, vid, t = medicine_video(db, owner, agent, tmp_path, monkeypatch, brief, review_json())
    with session_scope(write=False) as s:
        from app.models.production import SourceRecord

        ids = [x.id for x in s.scalars(select(SourceRecord).where(SourceRecord.video_id == vid).order_by(SourceRecord.id))]
    facts = ["Statins are medicines that lower cholesterol.", "Cholesterol is made in the liver.", "Statins reduce how much the liver makes.",
             "High cholesterol raises the risk of heart disease.", "Lowering it can reduce that risk.", "Doctors decide who needs a statin.",
             "Diet and exercise also affect cholesterol.", "Medicines work best when taken as directed."]
    script = {"text": "\n".join(f"{f} [S{ids[i % 2]}]" for i, f in enumerate(facts)) + "\nA doctor decides whether a medicine is right for you. [framing]"}
    say(fake, brief, review_json(), script, review_json())
    run_company(Runner("w", HANDLERS))
    stage, qa_ok, orig, ceo, owner_state, prog = video(vid)
    assert (qa_ok, orig, ceo) == (True, "PASS", True), prog
    assert stage == "PACKAGING" and owner_state == "NOT_REQUESTED"  # cannot be READY until a person has reviewed it
    assert approvals_for(vid, ApprovalKind.MEDICAL_REVIEW) == ["PENDING"] and approvals_for(vid, ApprovalKind.VIDEO_PUBLICATION) == []
    assert prog["step"] == "medical_review" and prog["who"] == "you"
    # the owner (standing in for the medical reviewer) approves: only then does it become READY and ask for the publication decision
    with session_scope() as s:
        from app.services import approvals

        req = s.scalar(select(ApprovalRequest).where(ApprovalRequest.subject_id == vid, ApprovalRequest.kind == ApprovalKind.MEDICAL_REVIEW.value))
        approvals.decide(s, owner, req.id, approve=True, note="Checked against both sources.")
    run_company(Runner("w", HANDLERS))
    assert video(vid)[0] == "READY" and approvals_for(vid, ApprovalKind.VIDEO_PUBLICATION) == ["PENDING"]


def test_the_conductor_only_looks_at_videos_started_with_the_company(db, owner, agent, tmp_path, monkeypatch):
    from app.services import videos

    connect_brain(db, owner, tmp_path, monkeypatch)
    pm = agent("production-manager")
    mine = videos.create(db, pm, "happy-birthday-songs", "Started with the company")
    videos.create(db, pm, "happy-birthday-songs", "Started by hand")
    done = videos.create(db, pm, "happy-birthday-songs", "Already on YouTube")
    db.commit()
    with session_scope() as s:
        s.get(Video, mine.id).metadata_json = {"conducted": True}
        s.get(Video, done.id).metadata_json = {"conducted": True}
        s.get(Video, done.id).stage = "PUBLISHED"
    db.commit()
    seen: list[int] = []
    monkeypatch.setattr(conductor, "conduct_video", lambda db_, vid: seen.append(vid) or [])
    conductor.conduct_all()
    assert seen == [mine.id]  # not the hand-started video, and not one that is already out


def test_progress_names_a_manual_step_as_the_owners_and_never_crashes_while_work_waits(db, owner, agent, tmp_path, monkeypatch):
    from app.services import tasks as tasks_svc

    connect_brain(db, owner, tmp_path, monkeypatch)
    pm = agent("production-manager")
    from app.services import videos

    v = videos.create(db, pm, "happy-birthday-songs", "Needs a person")
    db.commit()
    with session_scope() as s:
        s.get(Video, v.id).metadata_json = {"conducted": True}
    manual = tasks_svc.create(db, pm, title="Collect the sources", task_type="manual.research", assigned_agent_slug="research-specialist", video_id=v.id)
    db.commit()
    with session_scope(write=False) as s:
        p = conductor.progress(s, s.get(Video, v.id))
    assert (p["step"], p["who"]) == ("manual_step", "you") and "Collect the sources" in p["detail"]
    with session_scope() as s:  # only a task that is waiting on another remains: it must still produce an answer, not an error
        t = s.get(Task, manual.id)
        t.status = S.WAITING_FOR_DEPENDENCY.value
        t.task_type = "production.generate_music"
    with session_scope(write=False) as s:
        p = conductor.progress(s, s.get(Video, v.id))
    assert (p["step"], p["who"]) == ("working", "company") and "Waiting for earlier steps" in p["detail"]
