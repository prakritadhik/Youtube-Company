"""Moves a video the owner started with the AI company through every step no worker task covers.

The task chain makes the music, scenes, captions, video, thumbnail, QA report and rights report. Someone still has to *record*
those verdicts on the video, ask the CEO to re-check the rights evidence, approve the checked metadata, move the video to READY and
ask the owner to approve publication. Until now only the demonstration command did that. The conductor does exactly those steps, as
the roles that own them, through the same service functions and gates, and stops wherever a person must decide:

* a failed QA check opens correction tasks (a person fixes them);
* an originality verdict other than PASS waits for the Rights page (a person decides);
* a CEO re-check that disagrees stops there and says why;
* metadata with findings waits for a person;
* medical review and publication approval are requests to the owner. The conductor never grants them.

It acts only on videos with ``metadata_json["conducted"]`` (set by ``start_with_ai``), so nothing started by hand is touched.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import gateway, review
from app.db import session_scope
from app.enums import ApprovalKind, ApprovalStatus, OriginalityStatus, TaskStatus, VideoStage
from app.models.approvals import ApprovalRequest
from app.models.org import Channel
from app.models.production import SourceRecord
from app.models.tasks import Task
from app.models.videos import Video
from app.production import originality, pipeline
from app.research import ideas, seo, workflow
from app.security.permissions import Actor
from app.services import audit, board, gate, org, tasks, videos

log = logging.getLogger("ai.conductor")

MIN_SOURCES = 2  # the same minimum an idea needs before a source-based channel may approve it
TERMINAL_STAGES = (VideoStage.PUBLISHED.value, VideoStage.REJECTED.value, VideoStage.SCHEDULED.value)


class ConductorError(ValueError):
    pass


# --- starting --------------------------------------------------------------------------------------------------------------


def start_with_ai(db: Session, actor: Actor, idea_id: int, *, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn an approved idea into a video and queue its whole production, with the AI brain doing the writing."""
    st = gateway.brain_state(db)
    if not st["ready"]:
        raise ConductorError(f"The AI brain is not ready, so the company cannot write this video yet: {st['detail']}")
    video = ideas.start_video(db, actor, idea_id)
    idea = ideas.get(db, idea_id)
    video.metadata_json = {**(video.metadata_json or {}), "conducted": True}
    pm = org.actor_for_slug(db, "production-manager")
    t = pipeline.start_production(db, pm, video.id, title=idea.title, description=idea.concept[:1000], spec=spec, drafting="ai")
    t["metadata"] = workflow.start_packaging(db, org.actor_for_slug(db, "research-manager"), video.id)
    audit.record(db, actor, "video.conducted", "video", video.id, {"idea": idea_id, "tasks": {k: v.id for k, v in t.items()}})
    return {"video_id": video.id, "tasks": {k: v.id for k, v in t.items()}}


# --- resuming work parked because the brain was unavailable ---------------------------------------------------------------


def resume_parked(db: Session) -> list[int]:
    """Tasks parked with an ``AI_`` reason go back to the queue once the brain is ready again. Parking does not use up an attempt."""
    parked = [t for t in db.scalars(select(Task).where(Task.status == TaskStatus.BLOCKED.value, Task.blocked_reason.like("AI_%")))
              if not (t.blocked_reason or "").startswith("AI_REVIEW_FAILED")]
    if not parked or not gateway.brain_state(db)["ready"]:
        return []
    coo = org.actor_for_slug(db, "coo")  # blocked work is the COO's to release
    out = []
    for t in parked:
        tasks.unblock(db, coo, t.id, "The AI brain is ready again.")
        t.attempts = max(t.attempts - 1, 0)
        out.append(t.id)
    return out


# --- per-video steps -------------------------------------------------------------------------------------------------------


def _ever_requested(db: Session, video_id: int, kind: ApprovalKind) -> bool:
    return db.scalar(select(func.count()).select_from(ApprovalRequest).where(
        ApprovalRequest.kind == kind.value, ApprovalRequest.subject_type == "video", ApprovalRequest.subject_id == video_id)) > 0


def _complete_research(db: Session, video: Video, task: Task) -> bool:
    """The owner approved this idea with its sources recorded, and they were carried across to the video. Record that as the research step."""
    ids = list(db.scalars(select(SourceRecord.id).where(SourceRecord.video_id == video.id)))
    if len(ids) < MIN_SOURCES or task.status not in (TaskStatus.QUEUED.value, TaskStatus.CREATED.value):
        return False
    worker = org.actor_for_slug(db, "research-specialist")
    ch = db.get(Channel, video.channel_id)
    board.acknowledge(db, worker, ch.slug if ch else None, task.id)
    tasks.begin(db, worker, task.id)
    tasks.submit(db, worker, task.id, result={"sources": ids, "note": "Sources were recorded and approved with the idea; carried across to the video."},
                 evidence_specs=[{"kind": "db_record", "ref": f"source_records:{i}", "note": "recorded source"} for i in ids])
    boss = org.chain_of_command(db, org.get_agent(db, "research-specialist"))[0]
    tasks.review(db, org.actor_for_slug(db, boss.slug), task.id, approve=True, notes=f"{len(ids)} recorded sources re-read by the manager.")
    return True


def _note(db: Session, video: Video, text: str) -> None:
    """Remember the latest reason the conductor stopped, and write it to the audit log only when it changes."""
    md = dict(video.metadata_json or {})
    if md.get("conductor_stopped") != text:
        md["conductor_stopped"] = text
        video.metadata_json = md
        audit.record(db, Actor.system("conductor"), "conductor.stopped", "video", video.id, {"reason": text})


def conduct_video(db: Session, video_id: int) -> list[str]:
    v = db.get(Video, video_id)
    if v is None or v.stage in TERMINAL_STAGES:
        return []
    acts: list[str] = []
    ch = db.get(Channel, v.channel_id)
    pm, ceo = org.actor_for_slug(db, "production-manager"), org.actor_for_slug(db, "ceo")
    by_type = {t.task_type: t for t in db.scalars(select(Task).where(Task.video_id == v.id).order_by(Task.id))}

    research = by_type.get("manual.research")
    if research is not None and _complete_research(db, v, research):
        acts.append("recorded the research step")

    qa = by_type.get("production.qa_video")
    if qa is not None and qa.status == TaskStatus.COMPLETED.value and v.qa_task_id != qa.id:
        res = pipeline.finalize_qa(db, pm, v.id, qa.id)
        acts.append("recorded QA: passed" if res["passed"] else f"recorded QA: failed, {len(res['corrections'])} correction task(s) opened")
    orig = by_type.get("production.originality_review")
    if v.qa_passed and orig is not None and orig.status == TaskStatus.COMPLETED.value and v.originality_review_task_id != orig.id:
        pipeline.finalize_originality(db, v.id, orig.id)
        acts.append(f"recorded the originality verdict: {v.originality_status}")
    if v.originality_status == OriginalityStatus.PASS.value and not v.ceo_rights_verified and v.qa_passed:
        try:
            originality.ceo_inspect(db, ceo, v.id)
            acts.append("CEO re-checked the rights evidence and verified it")
        except videos.VideoError as e:
            _note(db, v, f"The CEO's rights re-check did not agree: {e}")

    ms = seo.latest(db, v.id)
    meta_task = by_type.get("seo.prepare_metadata")
    if ms is not None and meta_task is not None and meta_task.status == TaskStatus.COMPLETED.value and ms.status == "CHECKED":
        seo.approve(db, org.actor_for_slug(db, "research-manager"), ms.id, note="Checked metadata matches the finished video; no findings open.")
        acts.append("approved the checked title, description and tags")

    if v.stage in (VideoStage.PACKAGING.value, VideoStage.READY.value):
        res = gate.evaluate(db, v, purpose="clear")
        remaining = {i.code for i in res.issues} - {"STAGE_NOT_READY"}
        medical_required = bool((ch.policy or {}).get("human_medical_review_required")) or v.requires_medical_review  # type: ignore[union-attr]
        if remaining == {"MEDICAL_REVIEW_REQUIRED"} and medical_required and not _ever_requested(db, v.id, ApprovalKind.MEDICAL_REVIEW):
            videos.request_medical_review(db, pm, v.id, summary="Every automatic check has passed. A person must review the script against its sources before this video can go out.",
                                          evidence=[{"kind": "db_record", "ref": f"videos:{v.id}", "note": "the video"}])
            acts.append("asked you for the medical review")
        elif not remaining and v.stage == VideoStage.PACKAGING.value:
            videos.set_stage(db, pm, v.id, VideoStage.READY, note="every publication-gate condition except the owner's approval holds")
            acts.append("moved the video to READY")
        if not remaining and v.stage == VideoStage.READY.value and v.owner_approval_status == "NOT_REQUESTED" and not _ever_requested(db, v.id, ApprovalKind.VIDEO_PUBLICATION):
            videos.request_publication_approval(db, pm, v.id)
            acts.append("asked you to approve publication")
    for a in acts:
        audit.record(db, Actor.system("conductor"), "conductor.step", "video", v.id, {"did": a})
    if acts:
        md = dict(v.metadata_json or {})
        md.pop("conductor_stopped", None)
        v.metadata_json = md
    return acts


def conduct_all() -> list[dict[str, Any]]:
    with session_scope(write=False) as db:
        ids = [v.id for v in db.scalars(select(Video).where(Video.stage.notin_(TERMINAL_STAGES))) if review.is_conducted(v)]
    out = []
    for vid in ids:
        try:
            with session_scope() as db:
                acts = conduct_video(db, vid)
        except Exception as e:  # noqa: BLE001 - a video that cannot move must not stop the others; the reason is recorded
            log.exception("conductor stopped on video %s", vid)
            with session_scope() as db:
                v = db.get(Video, vid)
                if v is not None:
                    _note(db, v, f"{type(e).__name__}: {e}"[:400])
            continue
        if acts:
            out.append({"video": vid, "did": acts})
    return out


# --- what happens next ------------------------------------------------------------------------------------------------------


def progress(db: Session, v: Video) -> dict[str, Any]:
    """Where a conducted video is and what it is waiting for, from the records. ``who`` is 'company', 'you' or 'nobody'."""
    if not review.is_conducted(v):
        return {"conducted": False}
    ts = list(db.scalars(select(Task).where(Task.video_id == v.id).order_by(Task.id)))
    stopped = (v.metadata_json or {}).get("conductor_stopped")
    blocked = [t for t in ts if t.status == TaskStatus.BLOCKED.value]
    failed = [t for t in ts if t.status == TaskStatus.FAILED.value]
    corrections = [t for t in ts if t.task_type == "manual.correction" and t.status not in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)]
    manual = [t for t in ts if t.task_type.startswith("manual.") and t.task_type != "manual.correction"
              and t.status not in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value, TaskStatus.FAILED.value)]
    pending = ApprovalStatus.PENDING.value

    def open_approval(kind: ApprovalKind) -> bool:
        return db.scalar(select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.kind == kind.value, ApprovalRequest.subject_type == "video", ApprovalRequest.subject_id == v.id, ApprovalRequest.status == pending)) > 0

    if v.stage == VideoStage.REJECTED.value:
        step = ("rejected", "nobody", "This video was rejected.")
    elif v.stage in (VideoStage.PUBLISHED.value, VideoStage.SCHEDULED.value):
        step = ("on_youtube", "nobody", "The video is on YouTube.")
    elif any((t.blocked_reason or "").startswith("AI_") and not (t.blocked_reason or "").startswith("AI_REVIEW_FAILED") for t in blocked):
        reason = next(t.blocked_reason for t in blocked if (t.blocked_reason or "").startswith("AI_"))
        step = ("brain_unavailable", "you", f"Writing is paused because the AI brain is unavailable. {reason}")
    elif blocked or failed:
        t = (blocked or failed)[0]
        step = ("task_stuck", "you", f"'{t.title}' is {t.status.lower()}: {t.blocked_reason or (t.error or {}).get('message', 'see the task')}")
    elif corrections:
        step = ("corrections", "you", f"{len(corrections)} correction task(s) are open after a failed quality check.")
    elif open_approval(ApprovalKind.VIDEO_PUBLICATION):
        step = ("publication_approval", "you", "Waiting for your approval to publish.")
    elif open_approval(ApprovalKind.MEDICAL_REVIEW):
        step = ("medical_review", "you", "Waiting for a person to complete the medical review.")
    elif v.originality_status in (OriginalityStatus.NEEDS_REVISION.value, OriginalityStatus.BLOCKED.value, OriginalityStatus.HUMAN_REVIEW_REQUIRED.value):
        step = ("rights_decision", "you", f"The rights check says {v.originality_status}; see the Rights page.")
    elif stopped:
        step = ("stopped", "you", stopped)
    elif v.stage == VideoStage.READY.value and v.owner_approval_status == "APPROVED":
        step = ("approved", "nobody", "Approved by you. Uploading is a separate step on the YouTube page.")
    elif manual:
        t = manual[0]
        step = ("manual_step", "you", f"'{t.title}' is a manual step that only a person can do.")
    elif any(t.status not in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value) for t in ts):
        live = (TaskStatus.RUNNING.value, TaskStatus.SUBMITTED.value, TaskStatus.QUEUED.value, TaskStatus.RETRY_SCHEDULED.value, TaskStatus.REVISION_REQUESTED.value)
        active = next((t for t in ts if t.status in live), None)
        step = (("working", "company", f"In progress: {active.title} ({active.status.lower().replace('_', ' ')}).") if active
                else ("working", "company", "Waiting for earlier steps to finish."))
    else:
        step = ("finishing", "company", "The company is completing the last checks.")
    return {"conducted": True, "step": step[0], "who": step[1], "detail": step[2]}
