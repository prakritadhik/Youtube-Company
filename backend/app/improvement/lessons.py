"""What a finished video's workflow taught the company, proposed as lessons the CEO can verify.

A lesson is mined only from *recorded* events of a completed workflow, and every proposal carries machine-checkable evidence (the QA and originality
records it came from), so the CEO's verification is a real re-check and not a formality:

* **Quality:** a QA check that failed in an earlier report and passed in the final one, together with the correction that was recorded.
* **Originality:** a finding that appeared in an earlier originality review and was gone from the final PASS, with the corrective action recorded.

One finding on one video is a single observation: it is proposed at LOW confidence, as an OBSERVED entry, for the channel it came from. The same finding
on another video of that channel adds evidence to the existing lesson instead of creating a second one. Nothing here makes a lesson an instruction: that
needs the board's investigation, the CEO's verification and the owner's approval (the lessons are POLICY class, because they change how workers are told to work).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import BoardCategory, BoardStatus, Confidence, EvidenceKind, RiskClass, VideoStage
from app.models.board import BoardEntry
from app.models.improvement import WorkflowLesson
from app.models.org import Channel
from app.models.production import OriginalityReview, QAReport
from app.models.videos import Video
from app.security.permissions import Actor
from app.services import board

COMPLETE_STAGES = (VideoStage.READY.value, VideoStage.SCHEDULED.value, VideoStage.PUBLISHED.value)
CLOSED = (BoardStatus.REJECTED.value, BoardStatus.EXPIRED.value)


class LessonError(Exception):
    pass


def examined(db: Session, video_id: int) -> bool:
    return db.scalar(select(WorkflowLesson.id).where(WorkflowLesson.video_id == video_id, WorkflowLesson.kind == "examined")) is not None


def candidates(db: Session, limit: int = 20) -> list[int]:
    """Completed workflows that have not been examined for lessons yet."""
    done = select(WorkflowLesson.video_id).where(WorkflowLesson.kind == "examined")
    return list(db.scalars(select(Video.id).where(Video.stage.in_(COMPLETE_STAGES), Video.id.not_in(done)).order_by(Video.id).limit(limit)))


def _title(v: Video) -> str:
    return v.title or v.working_title


def _propose_or_extend(db: Session, actor: Actor, v: Video, ch: Channel, *, kind: str, key: str, title: str, description: str, category: BoardCategory,
                       refs: list[tuple[str, str]]) -> dict[str, Any]:
    """Create the lesson, or add this video's evidence to the lesson the same finding already produced for this channel."""
    link_key = f"{ch.slug}:{key}"
    existing = db.scalar(select(BoardEntry).join(WorkflowLesson, WorkflowLesson.entry_id == BoardEntry.id)
                         .where(WorkflowLesson.kind == kind, WorkflowLesson.key == link_key, BoardEntry.channel_id == ch.id).order_by(BoardEntry.id))
    if existing is not None:
        if existing.status in CLOSED:
            action = "skipped_closed"
        else:
            for ref, note in refs:
                board.add_evidence(db, actor, existing.id, kind=EvidenceKind.DB_RECORD, ref=ref, note=note)
            action = "evidence_added"
        entry = existing
    else:
        entry = board.propose(db, actor, title=title, description=description, category=category, channel_slug=ch.slug, confidence=Confidence.LOW,
                              risk_class=RiskClass.POLICY, evidence_items=[{"kind": "db_record", "ref": ref, "note": note} for ref, note in refs])
        action = "proposed"
    db.add(WorkflowLesson(video_id=v.id, kind=kind, key=link_key, entry_id=entry.id))
    db.flush()
    return {"action": action, "lesson": entry.public_id, "title": entry.title, "kind": kind}


def _qa(db: Session, actor: Actor, v: Video, ch: Channel) -> list[dict[str, Any]]:
    reports = list(db.scalars(select(QAReport).where(QAReport.video_id == v.id).order_by(QAReport.version)))
    if len(reports) < 2 or not reports[-1].passed:
        return []
    final = reports[-1]
    final_status = {c["id"]: c["status"] for c in final.checks}
    first_failure: dict[str, tuple[QAReport, dict[str, Any]]] = {}
    for r in reports[:-1]:
        for c in r.checks:
            if c["status"] == "fail" and final_status.get(c["id"]) == "pass":
                first_failure.setdefault(c["id"], (r, c))
    out = []
    for cid, (r, c) in first_failure.items():
        out.append(_propose_or_extend(
            db, actor, v, ch, kind="qa", key=cid, category=BoardCategory.QUALITY,
            title=f"QA check “{c['name']}” failed on submission and was corrected",
            description=(f"On “{_title(v)}” the QA check “{c['name']}” ({cid}) failed in report v{r.version}: {c['detail']}. The recorded correction was: {c['correction']}. "
                         f"The final report (v{final.version}) passed this check. Treat it as something to confirm before a {ch.name} video is submitted to QA. "
                         "One occurrence is an observation, not proof of a pattern."),
            refs=[(f"qa_reports:{r.id}", f"QA v{r.version} of video {v.id}: {cid} failed"), (f"qa_reports:{final.id}", f"QA v{final.version} of video {v.id}: all checks passed")]))
    return out


def _originality(db: Session, actor: Actor, v: Video, ch: Channel) -> list[dict[str, Any]]:
    reviews = list(db.scalars(select(OriginalityReview).where(OriginalityReview.video_id == v.id).order_by(OriginalityReview.version)))
    if len(reviews) < 2 or reviews[-1].status != "PASS":
        return []
    final = reviews[-1]
    final_codes = {f["code"] for f in final.report.get("findings", [])}
    found: dict[str, tuple[OriginalityReview, dict[str, Any], str]] = {}
    for r in reviews[:-1]:
        actions = {a["code"]: a["action"] for a in r.report.get("required_corrective_actions", [])}
        for f in r.report.get("findings", []):
            if f["severity"] in ("block", "revise") and f["code"] not in final_codes:
                found.setdefault(f["code"], (r, f, actions.get(f["code"], "not recorded")))
    out = []
    for code, (r, f, action) in found.items():
        out.append(_propose_or_extend(
            db, actor, v, ch, kind="originality", key=code, category=BoardCategory.ORIGINALITY,
            title=f"Originality finding {code} appeared in review and was resolved",
            description=(f"On “{_title(v)}” the originality review v{r.version} found {code}: {f['message']} The corrective action recorded was: {action}. "
                         f"Review v{final.version} passed without this finding. This is one video's experience; it is a prompt to check for the same thing earlier "
                         f"on {ch.name} videos, not proof that the action will always work."),
            refs=[(f"originality_reviews:{r.id}", f"review v{r.version} of video {v.id}: {code}"), (f"originality_reviews:{final.id}", f"review v{final.version} of video {v.id}: PASS")]))
    return out


def mine_video(db: Session, actor: Actor, video_id: int) -> dict[str, Any]:
    v = db.get(Video, video_id)
    if v is None:
        raise LessonError("video not found")
    if v.stage not in COMPLETE_STAGES:
        raise LessonError("lessons are mined only from a completed workflow (the video must be cleared, scheduled or published)")
    if examined(db, v.id):
        return {"video_id": v.id, "already_examined": True, "lessons": []}
    ch = db.get(Channel, v.channel_id)
    found = [*_qa(db, actor, v, ch), *_originality(db, actor, v, ch)]  # type: ignore[arg-type]
    db.add(WorkflowLesson(video_id=v.id, kind="examined", key="all"))
    db.flush()
    return {"video_id": v.id, "already_examined": False, "lessons": found}


def mine_all(db: Session, actor: Actor, limit: int = 20) -> dict[str, Any]:
    done = [mine_video(db, actor, vid) for vid in candidates(db, limit)]
    return {"videos_examined": len(done), "lessons": [x for d in done for x in d["lessons"]], "videos": done}
