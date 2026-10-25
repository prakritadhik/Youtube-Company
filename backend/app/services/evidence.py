"""Evidence records and their verification.

Verification is mechanical: files are re-hashed, saved API responses are re-read, test results are
parsed. A free-text ``NOTE`` or an unretrieved ``URL`` can document a claim but never proves it.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import EvidenceKind
from app.models.tasks import Evidence
from app.security.permissions import Actor
from app.storage import UnsafePath, resolve_data_path, sha256_file
from app.timeutil import utcnow

# Tables a DB_RECORD evidence reference may point to ("table:id").
_RECORD_TABLES: dict[str, str] = {
    "videos": "app.models.videos.Video",
    "tasks": "app.models.tasks.Task",
    "approval_requests": "app.models.approvals.ApprovalRequest",
    "board_entries": "app.models.board.BoardEntry",
    "reports": "app.models.reports.Report",
    "usage_records": "app.models.finance.UsageRecord",
    "creative_briefs": "app.models.production.CreativeBrief",
    "script_versions": "app.models.production.ScriptVersion",
    "source_records": "app.models.production.SourceRecord",
    "assets": "app.models.production.Asset",
    "originality_reviews": "app.models.production.OriginalityReview",
    "qa_reports": "app.models.production.QAReport",
    "content_ideas": "app.models.research.ContentIdea",
    "research_notes": "app.models.research.ResearchNote",
    "metadata_sets": "app.models.research.MetadataSet",
    "thumbnail_concepts": "app.models.research.ThumbnailConcept",
    "youtube_uploads": "app.models.youtube.YouTubeUpload",
}

MACHINE_VERIFIABLE = {EvidenceKind.FILE, EvidenceKind.DB_RECORD, EvidenceKind.API_RESPONSE, EvidenceKind.TEST_RESULT}


class EvidenceError(ValueError):
    pass


def _model(table: str):
    import importlib

    path = _RECORD_TABLES.get(table)
    if path is None:
        raise EvidenceError(f"record table '{table}' is not allowed as evidence")
    mod, cls = path.rsplit(".", 1)
    return getattr(importlib.import_module(mod), cls)


def add(
    db: Session,
    actor: Actor,
    *,
    kind: EvidenceKind,
    ref: str,
    task_id: int | None = None,
    board_entry_id: int | None = None,
    note: str = "",
    meta: dict[str, Any] | None = None,
) -> Evidence:
    if (task_id is None) == (board_entry_id is None):
        raise EvidenceError("evidence must belong to exactly one task or board entry")
    ev = Evidence(
        task_id=task_id,
        board_entry_id=board_entry_id,
        kind=kind.value,
        ref=ref,
        note=note,
        meta=meta or {},
        created_by=actor.label,
        retrieved_at=utcnow() if kind in (EvidenceKind.URL, EvidenceKind.API_RESPONSE) else None,
    )
    if kind in (EvidenceKind.FILE, EvidenceKind.API_RESPONSE, EvidenceKind.TEST_RESULT):
        try:
            path = resolve_data_path(ref)
        except UnsafePath as e:
            raise EvidenceError(str(e)) from e
        if not path.is_file():
            raise EvidenceError(f"evidence file does not exist: {ref}")
        ev.sha256 = sha256_file(path)
        ev.size_bytes = path.stat().st_size
    elif kind is EvidenceKind.DB_RECORD:
        table, _, rid = ref.partition(":")
        model = _model(table)
        if not rid.isdigit() or db.get(model, int(rid)) is None:
            raise EvidenceError(f"record not found: {ref}")
    db.add(ev)
    db.flush()
    return ev


def verify(db: Session, ev: Evidence, verifier: Actor) -> Evidence:
    """Re-check the evidence against reality and store the outcome."""
    status, detail = _check(db, ev)
    ev.verification_status = status
    ev.verification_detail = detail
    ev.verified_at = utcnow()
    ev.verified_by = verifier.label
    db.flush()
    return ev


def _check(db: Session, ev: Evidence) -> tuple[str, str]:
    kind = EvidenceKind(ev.kind)
    if kind is EvidenceKind.NOTE:
        return "unverifiable", "A written note documents a claim but cannot prove it."
    if kind is EvidenceKind.URL:
        return "unverifiable", "URL sources are recorded with a retrieval date but not machine-verified."
    if kind is EvidenceKind.DB_RECORD:
        table, _, rid = ev.ref.partition(":")
        try:
            model = _model(table)
        except EvidenceError as e:
            return "failed", str(e)
        return ("verified", "record exists") if rid.isdigit() and db.get(model, int(rid)) else (
            "failed", f"record {ev.ref} no longer exists"
        )

    # file-backed kinds
    try:
        path = resolve_data_path(ev.ref)
    except UnsafePath as e:
        return "failed", str(e)
    if not path.is_file():
        return "failed", "file is missing"
    if path.stat().st_size == 0:
        return "failed", "file is empty"
    actual = sha256_file(path)
    if ev.sha256 and actual != ev.sha256:
        return "failed", "file content changed since it was recorded (hash mismatch)"

    if kind is EvidenceKind.API_RESPONSE:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            return "failed", "saved API response is not valid JSON"
        code = doc.get("status_code")
        if not isinstance(code, int) or not 200 <= code < 300:
            return "failed", f"saved provider response has non-success status {code!r}"
        if not doc.get("provider"):
            return "failed", "saved provider response does not name its provider"
        if doc.get("simulated"):
            return "failed", "response is marked simulated and cannot prove a real provider interaction"
    elif kind is EvidenceKind.TEST_RESULT:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            return "failed", "test result file is not valid JSON"
        ok = doc.get("passed") is True or (doc.get("failed") == 0 and (doc.get("passed_count") or 0) > 0)
        if not ok:
            return "failed", "test result does not report success"
    return "verified", "file exists, hash matches"


def verify_all(db: Session, evidence: list[Evidence], verifier: Actor) -> dict[str, int]:
    out = {"verified": 0, "failed": 0, "unverifiable": 0}
    for ev in evidence:
        verify(db, ev, verifier)
        out[ev.verification_status] += 1
    return out


def for_task(db: Session, task_id: int) -> list[Evidence]:
    return list(db.scalars(select(Evidence).where(Evidence.task_id == task_id).order_by(Evidence.id)))


def for_board_entry(db: Session, entry_id: int) -> list[Evidence]:
    return list(db.scalars(select(Evidence).where(Evidence.board_entry_id == entry_id).order_by(Evidence.id)))


def serialize(ev: Evidence) -> dict[str, Any]:
    return {
        "id": ev.id,
        "kind": ev.kind,
        "ref": ev.ref,
        "sha256": ev.sha256,
        "size_bytes": ev.size_bytes,
        "note": ev.note,
        "retrieved_at": ev.retrieved_at.isoformat() if ev.retrieved_at else None,
        "verification_status": ev.verification_status,
        "verified_at": ev.verified_at.isoformat() if ev.verified_at else None,
        "verification_detail": ev.verification_detail,
        "created_by": ev.created_by,
        "meta": ev.meta,
    }
