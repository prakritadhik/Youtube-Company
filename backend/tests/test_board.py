from __future__ import annotations

import time
from datetime import date

import pytest

from app.enums import BoardCategory as C
from app.enums import BoardStatus as S
from app.enums import Confidence, EvidenceKind, RiskClass
from app.models.board import BoardEntry, BoardRelease
from app.security.permissions import PermissionDenied
from app.services import approvals, board, org, tasks
from app.storage import resolve_data_path
from tests.helpers import file_evidence

BDAY, MED = "happy-birthday-songs", "medicine-information"


def propose(db, agent, *, channel=BDAY, category=C.CREATIVE, confidence=Confidence.HIGH, risk=RiskClass.ROUTINE,
            title="Warm acoustic intros keep viewers past 10s", evidence=True, test_plan=None):
    return board.propose(
        db, agent("continuous-improvement-scientist"), title=title,
        description="Across 12 videos the acoustic-intro variants held 18% more viewers at 10s.",
        category=category, channel_slug=channel, confidence=confidence, risk_class=risk, test_plan=test_plan,
        evidence_items=[file_evidence(f"ev-{title[:10]}.json", b'{"n":12}')] if evidence else [],
    )


def verify(db, agent, e):
    board.transition(db, agent("analytics-manager"), e.id, S.UNDER_INVESTIGATION, "reviewing evidence")
    board.transition(db, agent("ceo"), e.id, S.VERIFIED)


def activate(db, agent, e):
    return board.transition(db, agent("ceo"), e.id, S.ACTIVE_INSTRUCTION)


def test_proposals_start_observed_with_ids_and_history(db, agent):
    e = propose(db, agent)
    assert e.status == S.OBSERVED.value and e.public_id == f"LSN-{e.id:06d}" and e.version == 1
    hist = board.entry_history(db, e.id)
    assert [h["version"] for h in hist] == [1]


def test_unverified_findings_never_become_active_automatically(db, agent, owner):
    e = propose(db, agent)
    assert board.instructions_for(db, BDAY)["instructions"] == []
    # CEO cannot jump straight to ACTIVE (or VERIFIED) from OBSERVED
    for to in (S.ACTIVE_INSTRUCTION, S.VERIFIED, S.APPROVED_FOR_TESTING):
        with pytest.raises(board.BoardError, match="not allowed"):
            board.transition(db, agent("ceo"), e.id, to)
        with pytest.raises(board.BoardError, match="not allowed"):
            board.transition(db, owner, e.id, to)
    board.transition(db, agent("analytics-manager"), e.id, S.UNDER_INVESTIGATION, "looking")
    with pytest.raises(board.BoardError, match="not allowed"):
        board.transition(db, agent("ceo"), e.id, S.ACTIVE_INSTRUCTION)
    assert board.instructions_for(db, BDAY)["instructions"] == []


def test_permissions_for_each_step(db, agent):
    e = propose(db, agent)
    with pytest.raises(PermissionDenied):  # a worker cannot investigate
        board.transition(db, agent("continuous-improvement-scientist"), e.id, S.UNDER_INVESTIGATION, "x")
    board.transition(db, agent("analytics-manager"), e.id, S.UNDER_INVESTIGATION, "x")
    with pytest.raises(PermissionDenied):  # a manager cannot verify
        board.transition(db, agent("analytics-manager"), e.id, S.VERIFIED)
    with pytest.raises(PermissionDenied):  # the COO cannot verify either
        board.transition(db, agent("coo"), e.id, S.VERIFIED)
    board.transition(db, agent("ceo"), e.id, S.VERIFIED)
    with pytest.raises(PermissionDenied):
        board.transition(db, agent("analytics-manager"), e.id, S.ACTIVE_INSTRUCTION)


def test_verification_requires_real_machine_verifiable_evidence(db, agent):
    no_ev = propose(db, agent, evidence=False, title="No evidence lesson")
    board.transition(db, agent("analytics-manager"), no_ev.id, S.UNDER_INVESTIGATION, "x")
    with pytest.raises(board.BoardError, match="no evidence"):
        board.transition(db, agent("ceo"), no_ev.id, S.VERIFIED)
    board.add_evidence(db, agent("continuous-improvement-scientist"), no_ev.id, kind=EvidenceKind.NOTE, ref="a hunch")
    board.add_evidence(db, agent("continuous-improvement-scientist"), no_ev.id, kind=EvidenceKind.URL,
                       ref="https://example.com/study")
    with pytest.raises(board.BoardError, match="machine-verifiable"):
        board.transition(db, agent("ceo"), no_ev.id, S.VERIFIED)


def test_ceo_rechecks_evidence_and_rejects_files_changed_since_proposal(db, agent):
    e = propose(db, agent)
    board.transition(db, agent("analytics-manager"), e.id, S.UNDER_INVESTIGATION, "x")
    ref = board.serialize(db, e)["evidence"][0]["ref"]
    resolve_data_path(ref).write_bytes(b'{"n": 1000, "doctored": true}')
    with pytest.raises(board.BoardError, match="failed verification"):
        board.transition(db, agent("ceo"), e.id, S.VERIFIED)
    assert db.get(BoardEntry, e.id).status == S.UNDER_INVESTIGATION.value


def test_full_lifecycle_to_active_records_versions_and_releases(db, agent):
    e = propose(db, agent)
    v0 = board.current_release_version(db)
    verify(db, agent, e)
    assert board.current_release_version(db) == v0  # not active yet: nothing changes for workers
    out = activate(db, agent, e)
    assert out["approval_id"] is None
    db.refresh(e)
    assert e.status == S.ACTIVE_INSTRUCTION.value and e.version == 4
    assert board.current_release_version(db) == v0 + 1
    assert [h["snapshot"]["status"] for h in board.entry_history(db, e.id)] == [
        "OBSERVED", "UNDER_INVESTIGATION", "VERIFIED", "ACTIVE_INSTRUCTION"]
    rel = db.query(BoardRelease).order_by(BoardRelease.version.desc()).first()
    assert rel.entry_ids == [e.id] and rel.affected_channel_ids == [e.channel_id]


def test_low_confidence_audience_finding_cannot_become_a_rule(db, agent):
    """A single successful video does not prove causation."""
    e = propose(db, agent, confidence=Confidence.LOW, title="One video did well with a red thumbnail")
    verify(db, agent, e)
    with pytest.raises(board.BoardError, match="low-confidence"):
        activate(db, agent, e)
    # ...but it may be tested as an experiment, with a proper plan
    with pytest.raises(board.BoardError, match="experiment needs"):
        board.transition(db, agent("ceo"), e.id, S.APPROVED_FOR_TESTING)
    board.revise(db, agent("analytics-manager"), e.id, test_plan={
        "hypothesis": "Red thumbnails raise CTR", "success_metric": "CTR", "baseline": "4.1%",
        "observation_period": "5 videos"})
    assert db.get(BoardEntry, e.id).status == S.UNDER_INVESTIGATION.value  # the edit withdrew verification
    board.transition(db, agent("ceo"), e.id, S.VERIFIED)
    board.transition(db, agent("ceo"), e.id, S.APPROVED_FOR_TESTING)
    assert board.instructions_for(db, BDAY)["experiments"][0]["public_id"] == e.public_id
    assert board.instructions_for(db, BDAY)["instructions"] == []


def test_experiment_needs_result_evidence_before_becoming_a_rule(db, agent):
    plan = {"hypothesis": "h", "success_metric": "m", "baseline": "b", "observation_period": "p"}
    e = propose(db, agent, confidence=Confidence.MEDIUM, test_plan=plan, title="Experiment lesson")
    verify(db, agent, e)
    board.transition(db, agent("ceo"), e.id, S.APPROVED_FOR_TESTING)
    time.sleep(0.01)
    with pytest.raises(board.BoardError, match="result evidence"):
        activate(db, agent, e)
    board.add_evidence(db, agent("experimentation-specialist"), e.id, kind=EvidenceKind.FILE,
                       ref=file_evidence("result.json", b'{"ctr": 5.0}')["ref"])
    activate(db, agent, e)
    assert db.get(BoardEntry, e.id).status == S.ACTIVE_INSTRUCTION.value


def test_editing_verified_or_active_content_withdraws_it(db, agent):
    e = propose(db, agent)
    verify(db, agent, e)
    activate(db, agent, e)
    assert board.instructions_for(db, BDAY)["instructions"]
    v_before = board.current_release_version(db)
    board.revise(db, agent("analytics-manager"), e.id, description="Actually 40% more viewers (unreviewed edit)")
    db.refresh(e)
    assert e.status == S.UNDER_INVESTIGATION.value and "re-verified" in e.status_reason
    assert board.instructions_for(db, BDAY)["instructions"] == []
    assert board.current_release_version(db) == v_before + 1  # workers are told it was withdrawn
    with pytest.raises(board.BoardError, match="not allowed"):
        board.transition(db, agent("ceo"), e.id, S.ACTIVE_INSTRUCTION)


def test_non_routine_lessons_need_owner_approval_to_activate(db, agent, owner):
    e = propose(db, agent, category=C.POLICY, channel=None, risk=RiskClass.POLICY, title="Raise upload cadence to daily")
    verify(db, agent, e)
    out = activate(db, agent, e)
    assert out["approval_id"] is not None
    db.refresh(e)
    assert e.status == S.VERIFIED.value  # CEO could not activate it alone
    assert board.instructions_for(db, None)["instructions"] == []
    with pytest.raises(PermissionDenied):
        approvals.decide(db, agent("ceo"), out["approval_id"], approve=True)
    approvals.decide(db, owner, out["approval_id"], approve=True, note="agreed")
    db.refresh(e)
    assert e.status == S.ACTIVE_INSTRUCTION.value
    assert [i["public_id"] for i in board.instructions_for(db, None)["instructions"]] == [e.public_id]


def test_owner_rejection_leaves_lesson_unapplied(db, agent, owner):
    e = propose(db, agent, category=C.POLICY, channel=None, risk=RiskClass.STRATEGY, title="Change channel focus")
    verify(db, agent, e)
    out = activate(db, agent, e)
    approvals.decide(db, owner, out["approval_id"], approve=False)
    assert db.get(BoardEntry, e.id).status == S.VERIFIED.value


def test_channel_specific_categories_cannot_be_company_wide(db, agent):
    for cat in (C.CREATIVE, C.SEO, C.THUMBNAIL_TITLE, C.RETENTION, C.AUDIENCE, C.EXPERIMENT):
        with pytest.raises(board.BoardError, match="must name a channel"):
            propose(db, agent, category=cat, channel=None)
    propose(db, agent, category=C.WORKFLOW, channel=None, title="Always keep source snapshots")


def test_channel_lessons_do_not_leak_into_other_channels(db, agent):
    e = propose(db, agent, channel=BDAY, title="Birthday-only creative lesson")
    verify(db, agent, e)
    activate(db, agent, e)
    assert [i["public_id"] for i in board.instructions_for(db, BDAY)["instructions"]] == [e.public_id]
    assert board.instructions_for(db, MED)["instructions"] == []
    assert board.instructions_for(db, "ai-story-videos")["instructions"] == []
    assert board.instructions_for(db, None)["instructions"] == []
    # digests are independent: the medicine channel is unchanged by a birthday lesson
    assert board.instructions_for(db, MED)["digest"] == board.instructions_for(db, MED)["digest"]


def test_company_wide_lessons_reach_every_channel(db, agent):
    e = propose(db, agent, channel=None, category=C.PROCEDURE, title="Keep source snapshots for every claim")
    verify(db, agent, e)
    activate(db, agent, e)
    for ch in (BDAY, MED, "ai-story-videos", None):
        assert [i["public_id"] for i in board.instructions_for(db, ch)["instructions"]] == [e.public_id]


def test_instructions_are_filtered_to_the_department(db, agent):
    e = propose(db, agent, category=C.CREATIVE)  # production-relevant only
    verify(db, agent, e)
    activate(db, agent, e)
    assert board.instructions_for(db, BDAY, "production")["instructions"]
    assert board.instructions_for(db, BDAY, "finance-operations")["instructions"] == []
    assert board.instructions_for(db, BDAY, "finance-operations")["digest"] != board.instructions_for(db, BDAY, "production")["digest"]


def test_workers_must_acknowledge_before_starting_affected_work(db, agent):
    e = propose(db, agent)
    verify(db, agent, e)
    activate(db, agent, e)
    pm, writer = agent("production-manager"), agent("script-lyrics-writer")
    t = tasks.create(db, pm, title="Write lyrics", channel_slug=BDAY, assigned_agent_slug="script-lyrics-writer")
    with pytest.raises(board.AcknowledgementRequired):
        tasks.begin(db, writer, t.id)
    board.acknowledge(db, writer, BDAY)
    tasks.begin(db, writer, t.id)
    db.refresh(t)
    assert t.board_version_used == board.current_release_version(db) and t.board_digest_used


def test_new_release_requires_a_new_acknowledgement(db, agent):
    first = propose(db, agent, title="First lesson")
    verify(db, agent, first)
    activate(db, agent, first)
    writer, pm = agent("script-lyrics-writer"), agent("production-manager")
    board.acknowledge(db, writer, BDAY)
    t1 = tasks.create(db, pm, title="t1", channel_slug=BDAY, assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, writer, t1.id)
    second = propose(db, agent, title="Second lesson")
    verify(db, agent, second)
    activate(db, agent, second)
    t2 = tasks.create(db, pm, title="t2", channel_slug=BDAY, assigned_agent_slug="script-lyrics-writer")
    with pytest.raises(board.AcknowledgementRequired):
        tasks.begin(db, writer, t2.id)
    assert board.pending_updates(db, org.get_agent(db, "script-lyrics-writer")) == [
        {"channel": BDAY, "scope": f"{BDAY}:production"}]


def test_only_relevant_workers_have_pending_updates(db, agent):
    e = propose(db, agent, category=C.CREATIVE)
    verify(db, agent, e)
    activate(db, agent, e)
    assert board.pending_updates(db, org.get_agent(db, "script-lyrics-writer"))
    assert board.pending_updates(db, org.get_agent(db, "cost-usage-analyst")) == []


def test_runner_acknowledges_on_behalf_of_the_assigned_role(db, agent, system):
    e = propose(db, agent)
    verify(db, agent, e)
    activate(db, agent, e)
    t = tasks.create(db, agent("production-manager"), title="auto", task_type="auto", channel_slug=BDAY,
                     assigned_agent_slug="script-lyrics-writer")
    claimed = tasks.claim_next(db, system, "w", {"auto"})
    assert claimed.id == t.id and claimed.board_version_used == board.current_release_version(db)


def test_expiring_and_rejecting(db, agent):
    e = propose(db, agent)
    verify(db, agent, e)
    activate(db, agent, e)
    with pytest.raises(board.BoardError, match="reason"):
        board.transition(db, agent("ceo"), e.id, S.EXPIRED)
    board.transition(db, agent("ceo"), e.id, S.EXPIRED, "superseded by newer data")
    assert board.instructions_for(db, BDAY)["instructions"] == []
    with pytest.raises(board.BoardError, match="not allowed"):
        board.transition(db, agent("ceo"), e.id, S.ACTIVE_INSTRUCTION)  # terminal
    r = propose(db, agent, title="Bad idea")
    board.transition(db, agent("ceo"), r.id, S.REJECTED, "contradicted by data")
    assert db.get(BoardEntry, r.id).status == S.REJECTED.value


def test_poster_for_owner_hides_raw_findings(db, agent):
    raw = propose(db, agent, title="Raw observation")
    ver = propose(db, agent, title="Verified lesson")
    verify(db, agent, ver)
    p = board.poster(db, owner_view=True)
    titles = [x["title"] for sec in p["sections"].values() for lst in sec.values() for x in lst]
    assert "Verified lesson" in titles and "Raw observation" not in titles
    full = board.poster(db, owner_view=False)
    titles = [x["title"] for sec in full["sections"].values() for lst in sec.values() for x in lst]
    assert "Raw observation" in titles and raw.id
    assert "not a separate source of truth" in p["note"]


def test_review_date_is_stored(db, agent):
    e = board.propose(db, agent("continuous-improvement-scientist"), title="t", description="d",
                      category=C.WORKFLOW, channel_slug=None, review_date=date(2027, 1, 15))
    assert board.serialize(db, e)["review_date"] == "2027-01-15"
