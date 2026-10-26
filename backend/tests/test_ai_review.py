"""Manager review: mechanical re-verification for routine work, bounded model-assisted review for creative drafts, and what is never reviewed here."""

from __future__ import annotations

from sqlalchemy import select

from app.ai import autopilot, conductor, gateway, review
from app.db import session_scope
from app.enums import TaskStatus as S
from app.models.ai import AiGeneration
from app.models.production import CreativeBrief
from app.models.tasks import Task
from app.models.videos import Video
from app.production import pipeline
from app.services import tasks, videos
from app.storage import resolve_data_path
from app.worker.runner import HANDLERS, Runner
from tests.ai_helpers import SPEC, approved_idea, brief_json, connect_brain, lyrics_json, review_json, say


def started(db, owner, tmp_path, monkeypatch, *replies):
    fake = connect_brain(db, owner, tmp_path, monkeypatch)
    say(fake, *replies)
    out = conductor.start_with_ai(db, owner, approved_idea(db, owner).id, spec=SPEC)
    db.commit()
    return fake, out["video_id"], out["tasks"]


def state(tid):
    with session_scope(write=False) as s:
        t = s.get(Task, tid)
        return t.status, t.blocked_reason, t.revision_count


def test_a_draft_returned_twice_is_handed_to_a_person_on_the_third_return_and_stays_there(db, owner, tmp_path, monkeypatch):
    fake, vid, t = started(db, owner, tmp_path, monkeypatch, brief_json(), review_json("revise", "1. Make the hook concrete."), brief_json(), review_json("revise", "1. Still generic."),
                           brief_json(), review_json("revise", "1. Third time: still generic."))
    r = Runner("w", HANDLERS)
    for expected in (S.REVISION_REQUESTED.value, S.REVISION_REQUESTED.value):
        r.run_once()
        autopilot.step("w")
        assert state(t["brief"])[0] == expected
    r.run_once()
    out = review.sweep()
    assert out == [{"task": t["brief"], "type": "ai.draft_brief", "result": "escalated"}]
    status, reason, returns = state(t["brief"])
    assert status == S.BLOCKED.value and reason.startswith("AI_REVIEW_FAILED") and "Third time" in reason and returns == 2
    with session_scope(write=False) as s:
        assert {b.status for b in s.scalars(select(CreativeBrief).where(CreativeBrief.video_id == vid))} == {"REJECTED"}
    # a person decides: the company does not resume it by itself, even though the brain is ready
    with session_scope() as s:
        assert conductor.resume_parked(s) == []
    assert state(t["brief"])[0] == S.BLOCKED.value


def test_a_review_the_model_cannot_give_is_never_turned_into_an_approval(db, owner, tmp_path, monkeypatch):
    fake, vid, t = started(db, owner, tmp_path, monkeypatch, brief_json(), "garbage", "garbage")
    Runner("w", HANDLERS).run_once()
    assert review.sweep() == [{"task": t["brief"], "type": "ai.draft_brief", "result": "unusable"}]
    assert state(t["brief"])[0] == S.SUBMITTED.value
    say(fake, brief_json(), "garbage", "garbage", "garbage", "garbage")
    assert review.sweep() == [{"task": t["brief"], "type": "ai.draft_brief", "result": "unusable"}]
    assert review.sweep() == [{"task": t["brief"], "type": "ai.draft_brief", "result": "escalated"}]
    status, reason, _ = state(t["brief"])
    assert status == S.BLOCKED.value and reason.startswith("AI_REVIEW_FAILED")
    with session_scope(write=False) as s:
        assert s.scalar(select(CreativeBrief).where(CreativeBrief.video_id == vid)).status == "DRAFT"


def test_while_the_brain_is_unavailable_nothing_is_decided_and_nothing_is_escalated(db, owner, tmp_path, monkeypatch):
    fake, vid, t = started(db, owner, tmp_path, monkeypatch, brief_json())
    Runner("w", HANDLERS).run_once()
    fake.set(error="result", message="Please run /login")
    assert review.sweep() == [{"task": t["brief"], "type": "ai.draft_brief", "result": "waiting"}]
    assert review.sweep() == [{"task": t["brief"], "type": "ai.draft_brief", "result": "waiting"}]  # cooling down: no new program call
    assert len(fake.calls()) == 2 and state(t["brief"])[0] == S.SUBMITTED.value
    with session_scope(write=False) as s:
        failed = list(s.scalars(select(AiGeneration).where(AiGeneration.purpose == "review.brief")))
        assert [g.error_code for g in failed] == ["NOT_SIGNED_IN"]


def test_routine_work_is_returned_when_its_evidence_no_longer_verifies(db, owner, tmp_path, monkeypatch):
    fake, vid, t = started(db, owner, tmp_path, monkeypatch, brief_json(), review_json(), lyrics_json(), review_json())
    r = Runner("w", HANDLERS)
    r.run_once()
    autopilot.step("w")
    r.run_once()
    autopilot.step("w")
    assert r.run_once() == t["music"]  # the generator ran; its submission waits for the manager
    with session_scope(write=False) as s:
        wav = resolve_data_path(next(e.ref for e in s.get(Task, t["music"]).evidence if e.kind == "file"))
    wav.unlink()
    assert review.sweep() == [{"task": t["music"], "type": "production.generate_music", "result": "returned"}]
    status, _, returns = state(t["music"])
    assert status == S.REVISION_REQUESTED.value and returns == 1


def test_only_the_companys_own_work_is_reviewed_here(db, owner, agent, tmp_path, monkeypatch):
    connect_brain(db, owner, tmp_path, monkeypatch)
    pm = agent("production-manager")
    v = videos.create(db, pm, "happy-birthday-songs", "A hand-started video")
    chain = pipeline.start_production(db, pm, v.id, title="A hand-started video", description="x", spec=SPEC)  # not started with the AI company
    fix = tasks.create(db, pm, title="Fix captions", task_type="manual.correction", assigned_agent_slug="subtitle-caption-specialist", video_id=v.id)
    music_id, fix_id = chain["music"].id, fix.id
    db.commit()
    with session_scope() as s:
        for tid in (music_id, fix_id):
            s.get(Task, tid).status = S.SUBMITTED.value  # as if the steps had just been submitted
    assert review.sweep() == []  # a video nobody started with the AI company is left to its people
    assert state(music_id)[0] == S.SUBMITTED.value
    with session_scope() as s:
        v2 = s.get(Video, v.id)
        v2.metadata_json = {**v2.metadata_json, "conducted": True}
    out = review.sweep()
    assert out == [{"task": music_id, "type": "production.generate_music", "result": "returned"}]  # no evidence was submitted, so it cannot be accepted
    assert state(fix_id)[0] == S.SUBMITTED.value  # a correction is always a person's job, even on a conducted video


def test_the_company_does_nothing_by_itself_when_nobody_started_a_video_with_it(db, owner, tmp_path, monkeypatch):
    fake = connect_brain(db, owner, tmp_path, monkeypatch)
    assert autopilot.step("w") == {"resumed": [], "reviews": [], "conducted": []} and fake.calls() == []


def test_the_model_cannot_approve_work_that_was_never_submitted(db, owner, tmp_path, monkeypatch):
    fake, vid, t = started(db, owner, tmp_path, monkeypatch, brief_json(), review_json())
    # the brief has not been written yet: a review pass finds nothing to review and never asks the model
    assert review.sweep() == [] and fake.calls() == []
    assert gateway.PARKING_CODES  # (the codes that park work are a fixed, documented set)


def test_the_reviewer_is_told_what_can_be_produced_and_not_to_nitpick(db, owner, tmp_path, monkeypatch):
    """Found with the real model: an unconstrained reviewer demanded a sung vocal the company cannot make, and kept finding new minor points."""
    fake, vid, t = started(db, owner, tmp_path, monkeypatch, brief_json(), review_json())
    Runner("w", HANDLERS).run_once()
    review.sweep()
    p = fake.calls()[1]["stdin"]
    assert "NO singing voice" in p and "never ask for sung vocals" in p and "AS THE COMPANY CAN PRODUCE IT TODAY" in p
    assert "Do NOT return it for polish, taste or wording preferences" in p and "at most 4 numbered" in p and "revision round 1" in p
    assert "judge only whether the earlier points were fixed" not in p  # the first round is a full review


def test_failures_caused_by_an_unavailable_brain_never_count_against_a_draft(db, owner, tmp_path, monkeypatch):
    fake, vid, t = started(db, owner, tmp_path, monkeypatch, brief_json())
    Runner("w", HANDLERS).run_once()
    with session_scope() as s:  # many earlier attempts failed because the brain was signed out or at its limit
        for code in ("NOT_SIGNED_IN", "USAGE_LIMIT", "NOT_SIGNED_IN", "UNKNOWN_ERROR", "USAGE_LIMIT", "NOT_SIGNED_IN"):
            s.add(AiGeneration(purpose="review.brief", role_slug="production-manager", task_id=t["brief"], attempt=1, status="FAILED", error_code=code,
                               prompt_sha256="0" * 64, prompt_chars=1))
        s.add(AiGeneration(purpose="doctor", role_slug="owner", attempt=1, status="OK", prompt_sha256="0" * 64, prompt_chars=1))  # the brain is usable again
    say(fake, brief_json(), review_json())
    assert review.sweep() == [{"task": t["brief"], "type": "ai.draft_brief", "result": "approved"}]  # not escalated, and decided normally
