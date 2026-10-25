"""Creative briefs: complete before production, reviewed by the manager, tied to the board version used."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.org import Agent, Channel
from app.models.production import CreativeBrief
from app.models.videos import Video
from app.security.permissions import Actor, P, PermissionDenied
from app.services import audit, board, org
from app.storage import write_json_artifact


class BriefError(ValueError):
    pass


REQUIRED = (
    "video_objective", "target_audience", "viewer_benefit", "main_concept", "tone_and_style", "planned_length_seconds",
    "visual_direction", "audio_direction", "hook_and_opening", "structure", "thumbnail_concept", "distinctive_elements",
    "originality_requirements", "success_criteria",
)
CHANNEL_REQUIRED = {
    "birthday": ("musical_plan",),
    "medicine": ("source_requirements", "safety_notes"),
    "story": ("premise", "characters"),
}
MUSICAL_PLAN_REQUIRED = ("tempo_bpm", "key", "mood", "instrumentation", "vocal_delivery", "song_structure")
LIST_FIELDS = ("distinctive_elements", "originality_requirements", "success_criteria")


def validate(content: dict[str, Any], channel_kind: str) -> list[str]:
    problems: list[str] = []
    for f in (*REQUIRED, *CHANNEL_REQUIRED.get(channel_kind, ())):
        v = content.get(f)
        if v is None or (isinstance(v, (str, list, dict)) and not v):
            problems.append(f"'{f}' is required")
    for f in LIST_FIELDS:
        if f in content and not isinstance(content[f], list):
            problems.append(f"'{f}' must be a list")
    length = content.get("planned_length_seconds")
    if length is not None and (not isinstance(length, (int, float)) or isinstance(length, bool) or not 5 <= length <= 3600):
        problems.append("'planned_length_seconds' must be a number between 5 and 3600")
    if channel_kind == "birthday" and isinstance(content.get("musical_plan"), dict):
        mp = content["musical_plan"]
        for f in MUSICAL_PLAN_REQUIRED:
            if not mp.get(f):
                problems.append(f"musical_plan.'{f}' is required")
        bpm = mp.get("tempo_bpm")
        if bpm is not None and (not isinstance(bpm, (int, float)) or isinstance(bpm, bool) or not 40 <= bpm <= 220):
            problems.append("musical_plan.'tempo_bpm' must be between 40 and 220")
        if mp.get("vocal_delivery") not in (None, "sung", "instrumental", "spoken"):
            problems.append("musical_plan.'vocal_delivery' must be sung, instrumental or spoken")
    return problems


def create(db: Session, actor: Actor, video_id: int, content: dict[str, Any]) -> CreativeBrief:
    actor.require(P.TASK_WORK)
    video = db.get(Video, video_id)
    if video is None:
        raise BriefError("video not found")
    ch = db.get(Channel, video.channel_id)
    problems = validate(content, ch.kind)  # type: ignore[union-attr]
    if problems:
        raise BriefError("; ".join(problems))
    info = board.instructions_for(db, ch.slug, "production")  # type: ignore[union-attr]
    version = (db.scalar(select(func.max(CreativeBrief.version)).where(CreativeBrief.video_id == video_id)) or 0) + 1
    brief = CreativeBrief(
        video_id=video_id, version=version, content=content, status="DRAFT", created_by=actor.label,
        board_version_used=info["release_version"], board_digest_used=info["digest"],
    )
    db.add(brief)
    db.flush()
    brief.content = {**content, "_artifact": write_json_artifact(f"videos/{video_id}", f"brief-v{version}.json", content)}
    video.board_version_used = info["release_version"]
    audit.record(db, actor, "brief.created", "brief", brief.id, {"video": video_id, "version": version})
    return brief


def review(db: Session, actor: Actor, brief_id: int, *, approve: bool, notes: str = "") -> CreativeBrief:
    """The creative director's manager (or above) approves or returns the brief before production proceeds."""
    actor.require(P.TASK_REVIEW)
    brief = db.get(CreativeBrief, brief_id)
    if brief is None:
        raise BriefError("brief not found")
    if brief.status != "DRAFT":
        raise BriefError(f"brief is already {brief.status}")
    director = org.get_agent(db, "creative-director")
    if not org.is_superior_of(db, actor, db.get(Agent, director.id)):  # type: ignore[arg-type]
        raise PermissionDenied(actor, "brief:review (not the creative director's manager)")
    if not approve and not notes.strip():
        raise BriefError("revision notes are required")
    brief.status = "APPROVED" if approve else "REJECTED"
    brief.reviewed_by = actor.label
    brief.review_notes = notes
    db.flush()
    audit.record(db, actor, "brief.reviewed", "brief", brief.id, {"approved": approve, "notes": notes})
    return brief


def latest(db: Session, video_id: int, *, approved_only: bool = False) -> CreativeBrief | None:
    q = select(CreativeBrief).where(CreativeBrief.video_id == video_id)
    if approved_only:
        q = q.where(CreativeBrief.status == "APPROVED")
    return db.scalar(q.order_by(CreativeBrief.version.desc()))


def require_approved(db: Session, video_id: int) -> CreativeBrief:
    b = latest(db, video_id, approved_only=True)
    if b is None:
        raise BriefError("production cannot proceed: no approved creative brief")
    return b
