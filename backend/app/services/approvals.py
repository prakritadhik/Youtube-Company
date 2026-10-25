"""Owner approval requests. Only the human owner can decide; the stored action runs on approval."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import ApprovalKind, ApprovalStatus
from app.models.approvals import ApprovalRequest
from app.security.permissions import Actor, P, PermissionDenied
from app.services import audit
from app.timeutil import utcnow

Handler = Callable[[Session, Actor, ApprovalRequest, dict[str, Any]], dict[str, Any] | None]
_HANDLERS: dict[str, Handler] = {}


class ApprovalError(Exception):
    pass


def register_handler(key: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[key] = fn
        return fn

    return deco


def handler_keys() -> list[str]:
    return sorted(_HANDLERS)


def request(
    db: Session,
    actor: Actor,
    *,
    kind: ApprovalKind,
    title: str,
    summary: str,
    why_it_matters: str,
    if_approved: str,
    if_rejected: str,
    evidence: list[dict[str, Any]] | None = None,
    channel_id: int | None = None,
    subject_type: str | None = None,
    subject_id: int | None = None,
    action: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> ApprovalRequest:
    actor.require(P.APPROVAL_REQUEST)
    if action is not None and action.get("handler") not in _HANDLERS:
        raise ApprovalError(f"unknown approval handler '{action.get('handler')}'")
    # One open request per (kind, subject) so the owner is not flooded with duplicates.
    if subject_type and subject_id is not None:
        dup = db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.kind == kind.value,
                ApprovalRequest.subject_type == subject_type,
                ApprovalRequest.subject_id == subject_id,
                ApprovalRequest.status == ApprovalStatus.PENDING.value,
            )
        )
        if dup is not None:
            return dup
    req = ApprovalRequest(
        kind=kind.value, title=title, summary=summary, why_it_matters=why_it_matters,
        if_approved=if_approved, if_rejected=if_rejected, evidence=evidence or [],
        channel_id=channel_id, subject_type=subject_type, subject_id=subject_id, action=action,
        requested_by=actor.label, expires_at=expires_at,
    )
    db.add(req)
    db.flush()
    audit.record(db, actor, "approval.requested", "approval", req.id, {"kind": kind.value, "title": title})
    return req


def decide(db: Session, actor: Actor, approval_id: int, *, approve: bool, note: str = "") -> ApprovalRequest:
    """Owner decision. Agents (including the CEO) can never decide."""
    actor.require(P.APPROVAL_DECIDE)
    if not actor.is_owner:
        raise PermissionDenied(actor, P.APPROVAL_DECIDE)
    req = db.get(ApprovalRequest, approval_id)
    if req is None:
        raise ApprovalError("approval request not found")
    if req.status != ApprovalStatus.PENDING.value:
        raise ApprovalError(f"request already {req.status.lower()}")
    if req.expires_at is not None and req.expires_at <= utcnow():
        req.status = ApprovalStatus.EXPIRED.value
        db.flush()
        raise ApprovalError("request has expired")

    req.decided_by = actor.label
    req.decided_at = utcnow()
    req.decision_note = note
    if approve:
        outcome: dict[str, Any] | None = None
        if req.action:
            handler = _HANDLERS[req.action["handler"]]
            outcome = handler(db, actor, req, req.action.get("args", {}))
        req.status = ApprovalStatus.APPROVED.value
        req.outcome = outcome
    else:
        req.status = ApprovalStatus.REJECTED.value
        key = (req.action or {}).get("handler")
        rejected = _HANDLERS.get(f"{key}:rejected") if key else None
        if rejected is not None:
            req.outcome = rejected(db, actor, req, (req.action or {}).get("args", {}))
    db.flush()
    audit.record(
        db, actor, "approval.approved" if approve else "approval.rejected", "approval", req.id,
        {"kind": req.kind, "note": note, "outcome": req.outcome},
    )
    return req


def cancel(db: Session, actor: Actor, approval_id: int, reason: str) -> ApprovalRequest:
    req = db.get(ApprovalRequest, approval_id)
    if req is None or req.status != ApprovalStatus.PENDING.value:
        raise ApprovalError("only pending requests can be cancelled")
    if not (actor.is_owner or req.requested_by == actor.label):
        raise PermissionDenied(actor, "approval:cancel")
    req.status = ApprovalStatus.CANCELLED.value
    req.decision_note = reason
    db.flush()
    audit.record(db, actor, "approval.cancelled", "approval", req.id, {"reason": reason})
    return req


def expire_due(db: Session, actor: Actor) -> int:
    n = 0
    now = utcnow()
    for req in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.status == ApprovalStatus.PENDING.value, ApprovalRequest.expires_at.is_not(None),
            ApprovalRequest.expires_at <= now,
        )
    ):
        req.status = ApprovalStatus.EXPIRED.value
        n += 1
        audit.record(db, actor, "approval.expired", "approval", req.id, {})
    db.flush()
    return n


def pending(db: Session) -> list[ApprovalRequest]:
    return list(
        db.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.status == ApprovalStatus.PENDING.value)
            .order_by(ApprovalRequest.created_at)
        )
    )


def serialize(r: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": r.id, "kind": r.kind, "title": r.title, "summary": r.summary,
        "why_it_matters": r.why_it_matters, "if_approved": r.if_approved, "if_rejected": r.if_rejected,
        "evidence": r.evidence, "channel_id": r.channel_id, "subject_type": r.subject_type,
        "subject_id": r.subject_id, "status": r.status, "requested_by": r.requested_by,
        "created_at": r.created_at.isoformat(), "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "decided_by": r.decided_by, "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "decision_note": r.decision_note,
    }
