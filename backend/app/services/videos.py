"""Videos and the controls that decide whether one may move toward publication."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import ApprovalKind, OriginalityStatus, TaskStatus, VideoStage
from app.models.approvals import ApprovalRequest
from app.models.org import Channel
from app.models.tasks import Task
from app.models.videos import Video
from app.security.permissions import Actor, P, PermissionDenied
from app.services import approvals, audit, gate
from app.timeutil import iso, utcnow


class VideoError(Exception):
    pass


_ORDER = [
    VideoStage.IDEA, VideoStage.RESEARCH, VideoStage.BRIEF, VideoStage.SCRIPT, VideoStage.PRODUCTION,
    VideoStage.QA, VideoStage.PACKAGING, VideoStage.READY, VideoStage.SCHEDULED, VideoStage.PUBLISHED,
]


def _get(db: Session, video_id: int) -> Video:
    v = db.get(Video, video_id)
    if v is None:
        raise VideoError("video not found")
    return v


def create(db: Session, actor: Actor, channel_slug: str, working_title: str) -> Video:
    actor.require(P.VIDEO_MANAGE)
    ch = db.scalar(select(Channel).where(Channel.slug == channel_slug))
    if ch is None:
        raise VideoError(f"unknown channel '{channel_slug}'")
    v = Video(
        channel_id=ch.id, working_title=working_title.strip(),
        requires_medical_review=bool(ch.policy.get("human_medical_review_required")),
        medical_review_status="PENDING" if ch.policy.get("human_medical_review_required") else "NOT_REQUIRED",
    )
    db.add(v)
    db.flush()
    audit.record(db, actor, "video.created", "video", v.id, {"channel": channel_slug, "title": working_title})
    return v


def set_stage(db: Session, actor: Actor, video_id: int, to: VideoStage, note: str = "") -> Video:
    actor.require(P.VIDEO_MANAGE)
    v = _get(db, video_id)
    if to is VideoStage.PUBLISHED:
        raise VideoError("PUBLISHED can only be recorded from a verified upload response")
    if to is VideoStage.REJECTED:
        v.stage = to.value
    else:
        cur = VideoStage(v.stage)
        if cur is VideoStage.REJECTED:
            raise VideoError("a rejected video cannot be advanced; create a new one")
        if to in (VideoStage.READY, VideoStage.SCHEDULED):
            # Entering the publishing queue always runs the gate, in the backend.
            res = gate.evaluate(db, _as_if_stage(v, VideoStage.PACKAGING), purpose="clear")
            if not res.allowed:
                audit.record(db, actor, "video.gate_blocked", "video", v.id, res.as_dict())
                raise gate.GateBlocked(res)
        if _ORDER.index(to) > _ORDER.index(cur) + 1:
            raise VideoError(f"cannot skip from {cur.value} to {to.value}")
        v.stage = to.value
    v.stage_note = note or None
    v.updated_at = utcnow()
    db.flush()
    audit.record(db, actor, "video.stage", "video", v.id, {"to": to.value, "note": note})
    return v


def _as_if_stage(v: Video, stage: VideoStage) -> Video:
    """Evaluate the queue-entry gate as though the video had finished packaging (without mutating it)."""
    probe = Video(**{c.name: getattr(v, c.name) for c in Video.__table__.columns})
    probe.stage = stage.value
    return probe


def _reset_downstream(db: Session, actor: Actor, v: Video, why: str) -> None:
    """Rights state changed: any earlier CEO sign-off is void and a queued video leaves the queue."""
    v.ceo_rights_verified = False
    v.ceo_rights_verified_at = None
    if v.stage in (VideoStage.READY.value, VideoStage.SCHEDULED.value):
        v.stage = VideoStage.PACKAGING.value
        audit.record(db, actor, "video.removed_from_queue", "video", v.id, {"why": why})


def _completed_task(db: Session, task_id: int | None, label: str) -> Task:
    t = db.get(Task, task_id) if task_id else None
    if t is None or t.status != TaskStatus.COMPLETED.value:
        raise VideoError(f"{label} must reference a COMPLETED (manager-verified) task")
    return t


def record_originality(
    db: Session, actor: Actor, video_id: int, status: OriginalityStatus, *, review_task_id: int,
    asset_license_issues: int, note: str = "",
) -> Video:
    """The Copyright & Originality Reviewer's verdict, tied to a manager-verified review task."""
    actor.require(P.ORIGINALITY_REVIEW)
    v = _get(db, video_id)
    if status is OriginalityStatus.NOT_REVIEWED:
        raise VideoError("NOT_REVIEWED cannot be recorded as a verdict")
    _completed_task(db, review_task_id, "an originality verdict")
    if status is OriginalityStatus.PASS and asset_license_issues > 0:
        raise VideoError("PASS is not possible while assets lack verified licences")
    changed = status.value != v.originality_status or asset_license_issues != v.asset_license_issues
    v.originality_status = status.value
    v.originality_reviewed_at = utcnow()
    v.originality_review_task_id = review_task_id
    v.asset_license_issues = asset_license_issues
    if changed or status is not OriginalityStatus.PASS:
        _reset_downstream(db, actor, v, f"originality status {status.value}")
    db.flush()
    audit.record(db, actor, "video.originality", "video", v.id,
                 {"status": status.value, "license_issues": asset_license_issues, "note": note})
    return v


def record_qa(db: Session, actor: Actor, video_id: int, passed: bool, *, qa_task_id: int) -> Video:
    actor.require(P.VIDEO_MANAGE)
    v = _get(db, video_id)
    _completed_task(db, qa_task_id, "a QA result")
    v.qa_passed = passed
    v.qa_task_id = qa_task_id
    if not passed:
        _reset_downstream(db, actor, v, "QA failed")
    db.flush()
    audit.record(db, actor, "video.qa", "video", v.id, {"passed": passed})
    return v


def ceo_verify_rights(db: Session, actor: Actor, video_id: int, note: str = "") -> Video:
    """The CEO's independent inspection. It cannot declare an unresolved video safe."""
    actor.require(P.RIGHTS_VERIFY)
    v = _get(db, video_id)
    if v.originality_status != OriginalityStatus.PASS.value:
        raise VideoError(f"cannot verify: originality status is {v.originality_status}, not PASS")
    if v.asset_license_issues > 0:
        raise VideoError("cannot verify: assets without verified licences remain")
    _completed_task(db, v.originality_review_task_id, "the originality verdict")
    v.ceo_rights_verified = True
    v.ceo_rights_verified_at = utcnow()
    db.flush()
    audit.record(db, actor, "video.ceo_rights_verified", "video", v.id, {"note": note})
    return v


# --- owner decisions (human only) ------------------------------------------------------------------


def request_medical_review(db: Session, actor: Actor, video_id: int, *, summary: str, evidence: list[dict]) -> ApprovalRequest:
    v = _get(db, video_id)
    return approvals.request(
        db, actor, kind=ApprovalKind.MEDICAL_REVIEW,
        title=f"Medical review: {v.title or v.working_title}",
        summary=summary,
        why_it_matters="Medicine content may influence health decisions. It cannot publish without a human review.",
        if_approved="The medical-review condition is satisfied; other gates still apply.",
        if_rejected="The video is returned for revision and cannot be published.",
        evidence=evidence, channel_id=v.channel_id, subject_type="video", subject_id=v.id,
        action={"handler": "video.medical_review", "args": {"video_id": v.id}},
    )


@approvals.register_handler("video.medical_review")
def _on_medical(db: Session, owner: Actor, req: ApprovalRequest, args: dict[str, Any]) -> dict[str, Any]:
    if not owner.is_owner:
        raise PermissionDenied(owner, "human medical review")
    v = _get(db, args["video_id"])
    v.medical_review_status = "APPROVED"
    audit.record(db, owner, "video.medical_review_approved", "video", v.id, {})
    return {"video_id": v.id, "medical_review_status": v.medical_review_status}


@approvals.register_handler("video.medical_review:rejected")
def _on_medical_rejected(db: Session, owner: Actor, req: ApprovalRequest, args: dict[str, Any]) -> dict[str, Any]:
    v = _get(db, args["video_id"])
    v.medical_review_status = "REJECTED"
    _reset_downstream(db, owner, v, "medical review rejected")
    return {"video_id": v.id, "medical_review_status": v.medical_review_status}


def request_publication_approval(db: Session, actor: Actor, video_id: int) -> ApprovalRequest:
    """Ask the owner to approve publication — only for a video whose content checks already pass."""
    v = _get(db, video_id)
    res = gate.evaluate(db, v, purpose="clear")
    blocking = [i for i in res.issues if i.code != "STAGE_NOT_READY"]
    if blocking:
        raise gate.GateBlocked(res)
    v.owner_approval_status = "PENDING"
    return approvals.request(
        db, actor, kind=ApprovalKind.VIDEO_PUBLICATION,
        title=f"Publish: {v.title}", summary=f"Approve publishing '{v.title}'.",
        why_it_matters="Publishing is public and cannot be fully undone.",
        if_approved="The video may be uploaded/scheduled when the publishing step runs.",
        if_rejected="The video stays unpublished.", channel_id=v.channel_id, subject_type="video",
        subject_id=v.id, action={"handler": "video.publication", "args": {"video_id": v.id}},
    )


@approvals.register_handler("video.publication")
def _on_publication(db: Session, owner: Actor, req: ApprovalRequest, args: dict[str, Any]) -> dict[str, Any]:
    v = _get(db, args["video_id"])
    v.owner_approval_status = "APPROVED"
    return {"video_id": v.id}


@approvals.register_handler("video.publication:rejected")
def _on_publication_rejected(db: Session, owner: Actor, req: ApprovalRequest, args: dict[str, Any]) -> dict[str, Any]:
    v = _get(db, args["video_id"])
    v.owner_approval_status = "REJECTED"
    return {"video_id": v.id}


def request_gate_exception(db: Session, actor: Actor, video_id: int, codes: list[str], justification: str) -> ApprovalRequest:
    v = _get(db, video_id)
    bad = [c for c in codes if c not in gate.WAIVABLE]
    if bad:
        raise VideoError(f"these gate conditions can never be waived: {bad}")
    if not justification.strip():
        raise VideoError("a documented justification is required")
    return approvals.request(
        db, actor, kind=ApprovalKind.GATE_EXCEPTION, title=f"Gate exception: {v.title or v.working_title}",
        summary=f"Waive {codes}. Justification: {justification}",
        why_it_matters="Exceptions weaken a publication safeguard.", if_approved="The listed conditions are waived for this video.",
        if_rejected="The gate continues to apply unchanged.", channel_id=v.channel_id, subject_type="video",
        subject_id=v.id, action={"handler": "video.gate_exception", "args": {"video_id": v.id, "codes": codes}},
    )


@approvals.register_handler("video.gate_exception")
def _on_exception(db: Session, owner: Actor, req: ApprovalRequest, args: dict[str, Any]) -> dict[str, Any]:
    v = _get(db, args["video_id"])
    v.gate_exception_approval_id = req.id
    return {"video_id": v.id, "codes": args["codes"]}


def serialize(db: Session, v: Video) -> dict[str, Any]:
    ch = db.get(Channel, v.channel_id)
    res = gate.evaluate(db, v, purpose="clear")
    return {
        "id": v.id, "channel": ch.slug if ch else None, "title": v.title or v.working_title,
        "working_title": v.working_title, "stage": v.stage, "stage_note": v.stage_note,
        "originality_status": v.originality_status, "ceo_rights_verified": v.ceo_rights_verified,
        "asset_license_issues": v.asset_license_issues, "qa_passed": v.qa_passed,
        "medical_review_status": v.medical_review_status, "owner_approval_status": v.owner_approval_status,
        "gate": res.as_dict(), "youtube_video_id": v.youtube_video_id, "created_at": iso(v.created_at),
        "updated_at": iso(v.updated_at), "board_version_used": v.board_version_used,
    }
