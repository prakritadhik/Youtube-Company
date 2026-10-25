"""Publication gate. Fails closed: anything missing or unresolved blocks.

Evaluated in backend logic by every code path that moves a video toward publication, so neither the
dashboard nor any agent (including the CEO) can bypass it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.enums import ApprovalStatus, OperatingMode, OriginalityStatus, VideoStage
from app.models.approvals import ApprovalRequest
from app.models.org import Channel
from app.models.videos import Video
from app.services import settings as settings_svc
from app.storage import UnsafePath, resolve_data_path

# The only gate issue an owner-approved exception can waive. Rights, originality, licence, QA,
# medical-review and safety conditions can never be waived by anyone.
WAIVABLE = frozenset({"STAGE_NOT_READY"})  # DEMO_CONTENT is deliberately absent: demonstration videos can never be published


@dataclass
class GateIssue:
    code: str
    message: str

    @property
    def waivable(self) -> bool:
        return self.code in WAIVABLE


@dataclass
class GateResult:
    allowed: bool
    issues: list[GateIssue] = field(default_factory=list)
    exceptions_applied: list[str] = field(default_factory=list)
    purpose: str = "clear"

    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "purpose": self.purpose,
            "issues": [{"code": i.code, "message": i.message, "waivable": i.waivable} for i in self.issues],
            "exceptions_applied": self.exceptions_applied,
        }


def _file_ok(rel: str | None) -> bool:
    if not rel:
        return False
    try:
        p = resolve_data_path(rel)
    except UnsafePath:
        return False
    return p.is_file() and p.stat().st_size > 0


def evaluate(db: Session, video: Video, *, purpose: str = "clear") -> GateResult:
    """``purpose='clear'``: may the video enter the publishing queue?  ``'publish'``: may it go out now?"""
    issues: list[GateIssue] = []
    add = lambda code, msg: issues.append(GateIssue(code, msg))  # noqa: E731
    channel = db.get(Channel, video.channel_id)
    policy = (channel.policy if channel else {}) or {}

    if (video.metadata_json or {}).get("demo"):
        add("DEMO_CONTENT", "This is a pipeline demonstration video; it can never be published.")
    if not _file_ok(video.final_video_path):
        add("FINAL_VIDEO_MISSING", "The final video file is missing or empty.")
    if not _file_ok(video.thumbnail_path):
        add("THUMBNAIL_MISSING", "The thumbnail is missing or empty.")
    if not (video.title or "").strip() or not str((video.metadata_json or {}).get("description", "")).strip():
        add("METADATA_INCOMPLETE", "Title and description are required.")
    if not video.qa_passed:
        add("QA_NOT_PASSED", "Final quality assurance has not passed.")

    status = video.originality_status
    if status != OriginalityStatus.PASS.value:
        add(f"ORIGINALITY_{status}", f"Originality and rights review status is {status}.")
    if not video.ceo_rights_verified:
        add("CEO_RIGHTS_NOT_VERIFIED", "The CEO has not independently verified the rights evidence.")
    # The stored count can be stale, so the live asset register is consulted too (fail closed).
    from app.production import assets as assets_svc

    live = len(assets_svc.license_problems(db, video.id)) if video.id else 0
    if video.asset_license_issues > 0 or live > 0:
        add("ASSET_LICENSE_ISSUES", f"{max(video.asset_license_issues, live)} asset licence/provenance problem(s) are unresolved.")

    medical_required = bool(policy.get("human_medical_review_required")) or video.requires_medical_review
    if medical_required and video.medical_review_status != "APPROVED":
        add("MEDICAL_REVIEW_REQUIRED", "A human medical review must approve this video before publication.")

    if video.stage not in (VideoStage.PACKAGING.value, VideoStage.READY.value, VideoStage.SCHEDULED.value):
        add("STAGE_NOT_READY", f"The video is at stage {video.stage}; it must complete packaging first.")

    if purpose == "publish":
        if settings_svc.is_emergency_paused(db):
            add("EMERGENCY_PAUSE", "Emergency pause is active.")
        mode = settings_svc.operating_mode(db)
        if mode is OperatingMode.PAUSED:
            add("MODE_PAUSED", "Operating mode is PAUSED.")
        elif mode is OperatingMode.DEVELOPMENT:
            add("MODE_DEVELOPMENT", "DEVELOPMENT mode: real publishing is disabled.")
        else:
            autonomy = settings_svc.get(db, "autonomy")
            auto_ok = (
                mode is OperatingMode.CONTROLLED_AUTONOMY
                and channel is not None
                and channel.slug in autonomy.get("autopublish_channels", [])
                and policy.get("autopublish_allowed", False)
                and not medical_required
            )
            if not auto_ok and video.owner_approval_status != "APPROVED":
                add("OWNER_APPROVAL_REQUIRED", "The owner has not approved publishing this video.")

    exceptions: list[str] = []
    if video.gate_exception_approval_id:
        req = db.get(ApprovalRequest, video.gate_exception_approval_id)
        if req is not None and req.status == ApprovalStatus.APPROVED.value:
            waived = set((req.action or {}).get("args", {}).get("codes", [])) & WAIVABLE
            kept = [i for i in issues if i.code not in waived]
            exceptions = [i.code for i in issues if i.code in waived]
            issues = kept
    return GateResult(allowed=not issues, issues=issues, exceptions_applied=exceptions, purpose=purpose)


class GateBlocked(Exception):
    def __init__(self, result: GateResult):
        super().__init__("publication gate blocked: " + ", ".join(result.codes()))
        self.result = result
