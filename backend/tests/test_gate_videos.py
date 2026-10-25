from __future__ import annotations

import pytest

from app.enums import ApprovalKind
from app.enums import OriginalityStatus as O
from app.enums import VideoStage as V
from app.models.videos import Video
from app.security.permissions import PermissionDenied
from app.services import approvals, control, gate, videos
from app.services import settings as settings_svc
from app.storage import write_bytes_artifact
from tests.helpers import verified_task

BDAY, MED = "happy-birthday-songs", "medicine-information"


def make_ready_video(db, agent, channel=BDAY):
    """A video with every precondition satisfied, built through the real controls."""
    pm = agent("production-manager")
    v = videos.create(db, pm, channel, "Sunny Day Song")
    v.title = "Happy Birthday Sunny Day Song"
    v.metadata_json = {"description": "An original birthday song."}
    v.final_video_path = write_bytes_artifact("videos", f"v{v.id}.mp4", b"\x00video-bytes")
    v.thumbnail_path = write_bytes_artifact("videos", f"v{v.id}.png", b"\x89thumb")
    db.flush()
    for stage in (V.RESEARCH, V.BRIEF, V.SCRIPT, V.PRODUCTION, V.QA):
        videos.set_stage(db, pm, v.id, stage)
    qa = verified_task(db, agent, "final-qa-specialist", "production-manager", "QA", evidence_name=f"qa{v.id}.txt")
    videos.record_qa(db, pm, v.id, True, qa_task_id=qa.id)
    videos.set_stage(db, pm, v.id, V.PACKAGING)
    rev = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights", evidence_name=f"r{v.id}.txt")
    videos.record_originality(db, agent("copyright-originality-reviewer"), v.id, O.PASS, review_task_id=rev.id,
                              asset_license_issues=0)
    videos.ceo_verify_rights(db, agent("ceo"), v.id)
    return v


def test_new_video_cannot_enter_the_publishing_queue(db, agent):
    pm = agent("production-manager")
    v = videos.create(db, pm, BDAY, "Draft")
    for stage in (V.RESEARCH, V.BRIEF, V.SCRIPT, V.PRODUCTION, V.QA, V.PACKAGING):
        videos.set_stage(db, pm, v.id, stage)
    with pytest.raises(gate.GateBlocked) as e:
        videos.set_stage(db, pm, v.id, V.READY)
    codes = e.value.result.codes()
    for c in ("FINAL_VIDEO_MISSING", "THUMBNAIL_MISSING", "METADATA_INCOMPLETE", "QA_NOT_PASSED",
              "ORIGINALITY_NOT_REVIEWED", "CEO_RIGHTS_NOT_VERIFIED"):
        assert c in codes
    assert db.get(Video, v.id).stage == V.PACKAGING.value


def test_fully_cleared_video_enters_the_queue(db, agent):
    v = make_ready_video(db, agent)
    videos.set_stage(db, agent("production-manager"), v.id, V.READY)
    assert db.get(Video, v.id).stage == V.READY.value


@pytest.mark.parametrize("status", [O.NEEDS_REVISION, O.BLOCKED, O.HUMAN_REVIEW_REQUIRED])
def test_unresolved_originality_status_blocks_and_pulls_video_from_queue(db, agent, status):
    v = make_ready_video(db, agent)
    pm = agent("production-manager")
    videos.set_stage(db, pm, v.id, V.READY)
    rev = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights 2", evidence_name="r2.txt")
    videos.record_originality(db, agent("copyright-originality-reviewer"), v.id, status, review_task_id=rev.id,
                              asset_license_issues=0)
    db.refresh(v)
    assert v.stage == V.PACKAGING.value and not v.ceo_rights_verified  # out of the queue, sign-off void
    with pytest.raises(gate.GateBlocked) as e:
        videos.set_stage(db, pm, v.id, V.READY)
    assert f"ORIGINALITY_{status.value}" in e.value.result.codes() and "CEO_RIGHTS_NOT_VERIFIED" in e.value.result.codes()


def test_ceo_cannot_declare_an_unresolved_video_safe(db, agent):
    pm, ceo = agent("production-manager"), agent("ceo")
    v = videos.create(db, pm, BDAY, "x")
    with pytest.raises(videos.VideoError, match="NOT_REVIEWED"):
        videos.ceo_verify_rights(db, ceo, v.id)
    rev = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights", evidence_name="rr.txt")
    reviewer = agent("copyright-originality-reviewer")
    for status in (O.BLOCKED, O.NEEDS_REVISION, O.HUMAN_REVIEW_REQUIRED):
        videos.record_originality(db, reviewer, v.id, status, review_task_id=rev.id, asset_license_issues=0)
        with pytest.raises(videos.VideoError, match="not PASS"):
            videos.ceo_verify_rights(db, ceo, v.id)
    assert not db.get(Video, v.id).ceo_rights_verified


def test_pass_is_impossible_with_unlicensed_assets(db, agent):
    pm = agent("production-manager")
    v = videos.create(db, pm, BDAY, "x")
    rev = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights", evidence_name="rl.txt")
    with pytest.raises(videos.VideoError, match="licences"):
        videos.record_originality(db, agent("copyright-originality-reviewer"), v.id, O.PASS, review_task_id=rev.id,
                                  asset_license_issues=2)


def test_missing_asset_licences_block_even_with_pass_status(db, agent):
    v = make_ready_video(db, agent)
    v.asset_license_issues = 1  # e.g. a font was later found to lack a licence record
    db.flush()
    with pytest.raises(gate.GateBlocked) as e:
        videos.set_stage(db, agent("production-manager"), v.id, V.READY)
    assert "ASSET_LICENSE_ISSUES" in e.value.result.codes()


def test_verdicts_require_the_right_role_and_a_verified_review_task(db, agent):
    pm = agent("production-manager")
    v = videos.create(db, pm, BDAY, "x")
    rev = verified_task(db, agent, "copyright-originality-reviewer", "production-manager", "Rights", evidence_name="rv.txt")
    with pytest.raises(PermissionDenied):  # a writer cannot issue originality verdicts
        videos.record_originality(db, agent("script-lyrics-writer"), v.id, O.PASS, review_task_id=rev.id, asset_license_issues=0)
    with pytest.raises(PermissionDenied):  # the manager cannot either
        videos.record_originality(db, pm, v.id, O.PASS, review_task_id=rev.id, asset_license_issues=0)
    with pytest.raises(videos.VideoError, match="COMPLETED"):
        videos.record_originality(db, agent("copyright-originality-reviewer"), v.id, O.PASS, review_task_id=99999,
                                  asset_license_issues=0)
    with pytest.raises(PermissionDenied):  # the reviewer cannot grant the CEO's sign-off
        videos.ceo_verify_rights(db, agent("copyright-originality-reviewer"), v.id)


def test_failed_qa_blocks_and_removes_from_queue(db, agent):
    v = make_ready_video(db, agent)
    pm = agent("production-manager")
    videos.set_stage(db, pm, v.id, V.READY)
    qa2 = verified_task(db, agent, "final-qa-specialist", "production-manager", "QA again", evidence_name="qa2.txt")
    videos.record_qa(db, pm, v.id, False, qa_task_id=qa2.id)
    db.refresh(v)
    assert v.stage == V.PACKAGING.value
    with pytest.raises(gate.GateBlocked) as e:
        videos.set_stage(db, pm, v.id, V.READY)
    assert "QA_NOT_PASSED" in e.value.result.codes()


def test_missing_or_empty_files_block(db, agent):
    v = make_ready_video(db, agent)
    v.final_video_path = write_bytes_artifact("videos", "empty.mp4", b"")
    v.thumbnail_path = "../../etc/passwd"
    db.flush()
    codes = gate.evaluate(db, v).codes()
    assert "FINAL_VIDEO_MISSING" in codes and "THUMBNAIL_MISSING" in codes


def test_stages_cannot_be_skipped_and_published_cannot_be_set_directly(db, agent):
    pm = agent("production-manager")
    v = videos.create(db, pm, BDAY, "x")
    with pytest.raises(videos.VideoError, match="skip"):
        videos.set_stage(db, pm, v.id, V.PRODUCTION)
    with pytest.raises(videos.VideoError, match="verified upload"):
        videos.set_stage(db, pm, v.id, V.PUBLISHED)


# --- medicine -----------------------------------------------------------------------------------------------


def test_medicine_videos_always_need_human_medical_review(db, agent, owner):
    v = make_ready_video(db, agent, MED)
    v.requires_medical_review = False  # even if someone flips the flag, the channel policy still applies
    db.flush()
    with pytest.raises(gate.GateBlocked) as e:
        videos.set_stage(db, agent("production-manager"), v.id, V.READY)
    assert "MEDICAL_REVIEW_REQUIRED" in e.value.result.codes()
    req = videos.request_medical_review(db, agent("production-manager"), v.id, summary="Sources attached", evidence=[])
    with pytest.raises(PermissionDenied):
        approvals.decide(db, agent("ceo"), req.id, approve=True)  # no agent can be the human reviewer
    approvals.decide(db, owner, req.id, approve=True, note="checked against the label")
    videos.set_stage(db, agent("production-manager"), v.id, V.READY)
    assert db.get(Video, v.id).medical_review_status == "APPROVED"


def test_rejected_medical_review_removes_video_from_queue(db, agent, owner):
    v = make_ready_video(db, agent, MED)
    req = videos.request_medical_review(db, agent("production-manager"), v.id, summary="s", evidence=[])
    approvals.decide(db, owner, req.id, approve=True)
    pm = agent("production-manager")
    videos.set_stage(db, pm, v.id, V.READY)
    req2 = videos.request_medical_review(db, pm, v.id, summary="second look", evidence=[])
    approvals.decide(db, owner, req2.id, approve=False, note="dosage claim is wrong")
    db.refresh(v)
    assert v.medical_review_status == "REJECTED" and v.stage == V.PACKAGING.value


def test_medicine_never_autopublishes_even_in_controlled_autonomy(db, agent, owner):
    v = make_ready_video(db, agent, MED)
    req = videos.request_medical_review(db, agent("production-manager"), v.id, summary="s", evidence=[])
    approvals.decide(db, owner, req.id, approve=True)
    settings_svc.set_value(db, owner, "operating_mode", "CONTROLLED_AUTONOMY")
    settings_svc.set_value(db, owner, "autonomy", {"autopublish_channels": [MED]})
    assert "OWNER_APPROVAL_REQUIRED" in gate.evaluate(db, v, purpose="publish").codes()


# --- publish purpose / operating modes -----------------------------------------------------------------------------


def test_publish_gate_follows_operating_mode(db, agent, owner):
    v = make_ready_video(db, agent)
    assert "MODE_DEVELOPMENT" in gate.evaluate(db, v, purpose="publish").codes()
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    assert gate.evaluate(db, v, purpose="publish").codes() == ["OWNER_APPROVAL_REQUIRED"]
    req = videos.request_publication_approval(db, agent("publishing-manager"), v.id)
    approvals.decide(db, owner, req.id, approve=True)
    assert gate.evaluate(db, v, purpose="publish").allowed
    settings_svc.set_value(db, owner, "operating_mode", "PAUSED")
    assert "MODE_PAUSED" in gate.evaluate(db, v, purpose="publish").codes()
    settings_svc.set_value(db, owner, "operating_mode", "APPROVAL_FIRST")
    control.pause(db, owner, "stop")
    assert "EMERGENCY_PAUSE" in gate.evaluate(db, v, purpose="publish").codes()


def test_controlled_autonomy_needs_explicit_channel_authorisation(db, agent, owner):
    v = make_ready_video(db, agent)
    settings_svc.set_value(db, owner, "operating_mode", "CONTROLLED_AUTONOMY")
    assert gate.evaluate(db, v, purpose="publish").codes() == ["OWNER_APPROVAL_REQUIRED"]
    settings_svc.set_value(db, owner, "autonomy", {"autopublish_channels": [BDAY]})
    assert gate.evaluate(db, v, purpose="publish").allowed
    v.asset_license_issues = 1  # mandatory gates still apply in autonomy
    assert not gate.evaluate(db, v, purpose="publish").allowed


def test_cannot_request_publication_approval_for_a_blocked_video(db, agent):
    v = videos.create(db, agent("production-manager"), BDAY, "x")
    with pytest.raises(gate.GateBlocked):
        videos.request_publication_approval(db, agent("publishing-manager"), v.id)


# --- exceptions ---------------------------------------------------------------------------------------------------------


def test_rights_and_safety_conditions_can_never_be_waived(db, agent):
    v = videos.create(db, agent("production-manager"), BDAY, "x")
    for code in ("ORIGINALITY_BLOCKED", "ASSET_LICENSE_ISSUES", "MEDICAL_REVIEW_REQUIRED", "CEO_RIGHTS_NOT_VERIFIED",
                 "QA_NOT_PASSED", "EMERGENCY_PAUSE"):
        with pytest.raises(videos.VideoError, match="never be waived"):
            videos.request_gate_exception(db, agent("coo"), v.id, [code], "we are behind schedule")


def test_owner_approved_stage_exception_waives_only_the_stage(db, agent, owner):
    v = make_ready_video(db, agent)
    db.get(Video, v.id).stage = V.SCRIPT.value  # not yet at packaging
    db.flush()
    assert "STAGE_NOT_READY" in gate.evaluate(db, v).codes()
    with pytest.raises(videos.VideoError, match="justification"):
        videos.request_gate_exception(db, agent("coo"), v.id, ["STAGE_NOT_READY"], " ")
    req = videos.request_gate_exception(db, agent("coo"), v.id, ["STAGE_NOT_READY"], "re-using approved master")
    approvals.decide(db, owner, req.id, approve=True)
    res = gate.evaluate(db, v)
    assert res.allowed and res.exceptions_applied == ["STAGE_NOT_READY"]
    v.asset_license_issues = 1
    assert not gate.evaluate(db, v).allowed  # exceptions never cover rights problems


def test_approval_requests_dedupe_per_subject(db, agent):
    v = videos.create(db, agent("production-manager"), BDAY, "x")
    a = videos.request_medical_review(db, agent("production-manager"), v.id, summary="s", evidence=[])
    b = videos.request_medical_review(db, agent("production-manager"), v.id, summary="again", evidence=[])
    assert a.id == b.id and ApprovalKind.MEDICAL_REVIEW.value == a.kind
