"""Driving a lesson through the board from the dashboard.

The board's own rules are unchanged: only the CEO verifies, and activating a POLICY-class lesson is the owner's decision. The dashboard cannot make the
CEO "say" anything it would not: *verify* and *approve for testing* run the board's real checks as the CEO role (the evidence is re-hashed or re-read, a
test plan must be complete) and are audited with who asked for them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import BoardStatus
from app.models.board import BoardEntry
from app.security.permissions import Actor, P, PermissionDenied
from app.services import audit, board, org

S = BoardStatus
# action -> (target status, who performs it)
ACTIONS: dict[str, tuple[BoardStatus, str]] = {
    "investigate": (S.UNDER_INVESTIGATION, "requester"),
    "verify": (S.VERIFIED, "ceo"),
    "test": (S.APPROVED_FOR_TESTING, "ceo"),
    "activate": (S.ACTIVE_INSTRUCTION, "requester"),
    "reject": (S.REJECTED, "requester"),
    "expire": (S.EXPIRED, "requester"),
}


def act(db: Session, requester: Actor, public_id: str, action: str, reason: str = "") -> dict[str, Any]:
    if action not in ACTIONS:
        raise board.BoardError(f"unknown action '{action}'")
    entry = db.scalar(select(BoardEntry).where(BoardEntry.public_id == public_id))
    if entry is None:
        raise board.BoardError("lesson not found")
    to, who = ACTIONS[action]
    if who == "ceo":
        # The requester must be someone who may ask for the CEO's step: the owner, or the CEO itself.
        if not (requester.is_owner or requester.can(P.BOARD_VERIFY)):
            raise PermissionDenied(requester, P.BOARD_VERIFY)
        performer = org.actor_for_slug(db, "ceo")
        audit.record(db, requester, "board.ceo_step_requested", "board_entry", entry.public_id, {"action": action})
    else:
        performer = requester
    out = board.transition(db, performer, entry.id, to, reason)
    return {"entry": board.serialize(db, out["entry"]), "approval_id": out.get("approval_id"), "performed_by": performer.label}


def lessons(db: Session, statuses: tuple[str, ...] | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Every lesson with its place in the lifecycle (the owner-only working view; raw findings included)."""
    q = select(BoardEntry).order_by(BoardEntry.updated_at.desc()).limit(limit)
    if statuses:
        q = q.where(BoardEntry.status.in_(statuses))
    return [board.serialize(db, e) for e in db.scalars(q)]
