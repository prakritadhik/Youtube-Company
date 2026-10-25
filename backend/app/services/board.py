"""Shared company intelligence board.

The database is the source of truth; the dashboard "poster" is only a rendering of this data.

Rules enforced here (see tests):
* OBSERVED findings never become instructions by themselves; the path is
  OBSERVED → UNDER_INVESTIGATION → VERIFIED → APPROVED_FOR_TESTING / ACTIVE_INSTRUCTION.
* Only the CEO (or owner) verifies/activates; verification re-checks the evidence.
* Editing verified or active content demotes it to UNDER_INVESTIGATION.
* Channel-specific categories can never be company-wide, and lessons are served per channel.
* Workers receive only instructions relevant to their department and must acknowledge the current
  digest before starting channel work.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import (
    CHANNEL_SCOPED_CATEGORIES,
    ApprovalKind,
    BoardCategory,
    BoardStatus,
    Confidence,
    EvidenceKind,
    RiskClass,
)
from app.models.board import BoardAcknowledgement, BoardEntry, BoardEntryVersion, BoardRelease
from app.models.org import Agent, Channel, Department
from app.security.permissions import Actor, P
from app.services import approvals, audit, evidence
from app.timeutil import iso, utcnow


class BoardError(ValueError):
    pass


class AcknowledgementRequired(Exception):
    def __init__(self, scope: str, digest: str):
        super().__init__(f"agent has not acknowledged current board instructions for scope '{scope}'")
        self.scope = scope
        self.digest = digest


S = BoardStatus
ACTIVE_SET = {S.ACTIVE_INSTRUCTION.value, S.APPROVED_FOR_TESTING.value}
TERMINAL = {S.REJECTED.value, S.EXPIRED.value}
# Statuses visible on the owner-facing board (Layer C). Raw findings stay in diagnostics.
OWNER_VISIBLE = {
    S.VERIFIED.value, S.APPROVED_FOR_TESTING.value, S.ACTIVE_INSTRUCTION.value, S.REJECTED.value, S.EXPIRED.value,
}
# Categories that assert cause/effect from audience data need at least medium confidence to be rules.
CAUSAL_CATEGORIES = {
    BoardCategory.CREATIVE, BoardCategory.SEO, BoardCategory.THUMBNAIL_TITLE, BoardCategory.RETENTION,
    BoardCategory.AUDIENCE, BoardCategory.SENTIMENT, BoardCategory.FEEDBACK, BoardCategory.EXPERIMENT,
}

# Which departments a category is relevant to ("*" = every department).
CATEGORY_DEPARTMENTS: dict[BoardCategory, set[str]] = {
    BoardCategory.CHANNEL_OBJECTIVE: {"*"},
    BoardCategory.AUDIENCE: {"production", "research-growth", "community"},
    BoardCategory.CREATIVE: {"production"},
    BoardCategory.WEAK_PATTERN: {"production", "research-growth"},
    BoardCategory.RESEARCH: {"production", "research-growth"},
    BoardCategory.SEO: {"research-growth", "publishing"},
    BoardCategory.THUMBNAIL_TITLE: {"production", "research-growth", "publishing"},
    BoardCategory.FEEDBACK: {"production", "community", "analytics-improvement"},
    BoardCategory.RETENTION: {"production", "analytics-improvement"},
    BoardCategory.SENTIMENT: {"community", "analytics-improvement"},
    BoardCategory.QUALITY: {"production", "publishing"},
    BoardCategory.ORIGINALITY: {"production", "publishing"},
    BoardCategory.LICENSING: {"production", "publishing", "finance-operations"},
    BoardCategory.WORKFLOW: {"*"},
    BoardCategory.BOTTLENECK: {"finance-operations", "technology-reporting"},
    BoardCategory.COST: {"finance-operations", "production"},
    BoardCategory.FAILED_EXPERIMENT: {"analytics-improvement", "production", "research-growth"},
    BoardCategory.PROCEDURE: {"*"},
    BoardCategory.EXPERIMENT: {"analytics-improvement", "production", "research-growth"},
    BoardCategory.POLICY: {"*"},
}

_FORWARD = {
    S.OBSERVED: {S.UNDER_INVESTIGATION, S.REJECTED},
    S.UNDER_INVESTIGATION: {S.VERIFIED, S.REJECTED},
    S.VERIFIED: {S.APPROVED_FOR_TESTING, S.ACTIVE_INSTRUCTION, S.REJECTED, S.UNDER_INVESTIGATION},
    S.APPROVED_FOR_TESTING: {S.ACTIVE_INSTRUCTION, S.EXPIRED, S.REJECTED, S.UNDER_INVESTIGATION},
    S.ACTIVE_INSTRUCTION: {S.EXPIRED, S.REJECTED, S.UNDER_INVESTIGATION},
    S.REJECTED: set(),
    S.EXPIRED: set(),
}


# --- helpers -----------------------------------------------------------------------------------


def _snapshot(e: BoardEntry) -> dict[str, Any]:
    return {
        "public_id": e.public_id, "title": e.title, "description": e.description, "category": e.category,
        "channel_id": e.channel_id, "status": e.status, "confidence": e.confidence, "risk_class": e.risk_class,
        "version": e.version, "test_plan": e.test_plan, "review_date": e.review_date.isoformat() if e.review_date else None,
        "status_reason": e.status_reason,
    }


def _save_version(db: Session, e: BoardEntry, actor: Actor, note: str) -> None:
    db.add(BoardEntryVersion(entry_id=e.id, version=e.version, snapshot=_snapshot(e), changed_by=actor.label, change_note=note))
    db.flush()


def _departments_for(category: str) -> set[str]:
    return CATEGORY_DEPARTMENTS[BoardCategory(category)]


def _is_relevant(e: BoardEntry, department: str | None) -> bool:
    if department is None:
        return True
    depts = _departments_for(e.category)
    return "*" in depts or department in depts


def _digest(entries: list[BoardEntry]) -> str:
    payload = [
        {"id": e.public_id, "v": e.version, "status": e.status, "title": e.title, "desc": e.description, "plan": e.test_plan}
        for e in sorted(entries, key=lambda x: x.id)
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _scope(channel_slug: str | None, department: str | None) -> str:
    return f"{channel_slug or '*'}:{department or '*'}"


def _channel_id(db: Session, slug: str | None) -> int | None:
    if slug is None:
        return None
    ch = db.scalar(select(Channel).where(Channel.slug == slug))
    if ch is None:
        raise BoardError(f"unknown channel '{slug}'")
    return ch.id


def _active_entries(db: Session, channel_id: int | None, department: str | None) -> list[BoardEntry]:
    q = select(BoardEntry).where(BoardEntry.status.in_(ACTIVE_SET))
    if channel_id is None:
        q = q.where(BoardEntry.channel_id.is_(None))
    else:  # channel-specific lessons plus company-wide ones; never another channel's
        q = q.where((BoardEntry.channel_id == channel_id) | (BoardEntry.channel_id.is_(None)))
    return [e for e in db.scalars(q.order_by(BoardEntry.id)) if _is_relevant(e, department)]


def current_release_version(db: Session) -> int:
    return db.scalar(select(func.max(BoardRelease.version))) or 0


def _publish_release(db: Session, actor: Actor, entry: BoardEntry, reason: str) -> BoardRelease:
    """Active instructions changed: record a new global board version with per-scope digests."""
    version = current_release_version(db) + 1
    digests: dict[str, str] = {}
    channels = list(db.scalars(select(Channel)))
    departments = [d.slug for d in db.scalars(select(Department))]
    for ch in [None, *channels]:
        for dep in [None, *departments]:
            digests[_scope(ch.slug if ch else None, dep)] = _digest(
                _active_entries(db, ch.id if ch else None, dep)
            )
    rel = BoardRelease(
        version=version, reason=reason, created_by=actor.label, entry_ids=[entry.id],
        affected_channel_ids=[entry.channel_id], digests=digests,
    )
    db.add(rel)
    db.flush()
    audit.record(db, actor, "board.release", "board_release", version, {"reason": reason, "entry": entry.public_id})
    return rel


# --- proposing and editing -------------------------------------------------------------------------


def propose(
    db: Session,
    actor: Actor,
    *,
    title: str,
    description: str,
    category: BoardCategory,
    channel_slug: str | None,
    confidence: Confidence = Confidence.LOW,
    risk_class: RiskClass = RiskClass.ROUTINE,
    review_date: date | None = None,
    test_plan: dict[str, Any] | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
) -> BoardEntry:
    actor.require(P.BOARD_PROPOSE)
    if not title.strip() or not description.strip():
        raise BoardError("title and description are required")
    if category in CHANNEL_SCOPED_CATEGORIES and channel_slug is None:
        raise BoardError(f"'{category.value}' lessons apply to one audience and must name a channel")
    entry = BoardEntry(
        public_id=f"TMP-{utcnow().timestamp()}", title=title.strip(), description=description.strip(),
        category=category.value, channel_id=_channel_id(db, channel_slug), confidence=confidence.value,
        risk_class=risk_class.value, status=S.OBSERVED.value, review_date=review_date, test_plan=test_plan,
        created_by=actor.label, responsible_agent_id=actor.agent_id,
    )
    db.add(entry)
    db.flush()
    entry.public_id = f"LSN-{entry.id:06d}"
    for item in evidence_items or []:
        evidence.add(db, actor, board_entry_id=entry.id, kind=EvidenceKind(item["kind"]), ref=item["ref"],
                     note=item.get("note", ""), meta=item.get("meta"))
    _save_version(db, entry, actor, "proposed")
    audit.record(db, actor, "board.proposed", "board_entry", entry.public_id, {"category": category.value, "channel": channel_slug})
    return entry


def add_evidence(db: Session, actor: Actor, entry_id: int, *, kind: EvidenceKind, ref: str, note: str = "",
                 meta: dict[str, Any] | None = None):
    actor.require(P.BOARD_PROPOSE)
    entry = _get(db, entry_id)
    if entry.status in TERMINAL:
        raise BoardError("cannot add evidence to a closed entry")
    ev = evidence.add(db, actor, board_entry_id=entry.id, kind=kind, ref=ref, note=note, meta=meta)
    audit.record(db, actor, "board.evidence_added", "board_entry", entry.public_id, {"kind": kind.value, "ref": ref})
    return ev


def revise(
    db: Session, actor: Actor, entry_id: int, *, title: str | None = None, description: str | None = None,
    confidence: Confidence | None = None, test_plan: dict[str, Any] | None = None,
) -> BoardEntry:
    """Edit content. Verified/active content that changes goes back to UNDER_INVESTIGATION."""
    actor.require(P.BOARD_INVESTIGATE)
    e = _get(db, entry_id)
    if e.status in TERMINAL:
        raise BoardError("closed entries cannot be edited; propose a new entry")
    was_active = e.status in ACTIVE_SET
    changed = False
    if title is not None and title.strip() != e.title:
        e.title, changed = title.strip(), True
    if description is not None and description.strip() != e.description:
        e.description, changed = description.strip(), True
    if test_plan is not None and test_plan != e.test_plan:
        e.test_plan, changed = test_plan, True
    if confidence is not None and confidence.value != e.confidence:
        e.confidence, changed = confidence.value, True
    if not changed:
        return e
    e.version += 1
    if e.status in (S.VERIFIED.value, *ACTIVE_SET):
        e.status = S.UNDER_INVESTIGATION.value
        e.status_changed_at = utcnow()
        e.status_reason = "content changed after verification; must be re-verified"
    _save_version(db, e, actor, "revised")
    audit.record(db, actor, "board.revised", "board_entry", e.public_id, {"version": e.version})
    if was_active:
        _publish_release(db, actor, e, f"{e.public_id} edited and withdrawn pending re-verification")
    return e


def record_result(db: Session, actor: Actor, entry_id: int, *, confidence: Confidence, result_text: str) -> BoardEntry:
    """Record an experiment's outcome on its own entry: add the result to the description and set the confidence it earned.

    Allowed only while the entry is being tested, and only by someone who may approve testing. It never changes the status; because the text workers
    read changes, a new board version is published and workers re-acknowledge it. Editing any other way would demote the entry.
    """
    actor.require(P.BOARD_APPROVE_TESTING)
    e = _get(db, entry_id)
    if e.status != S.APPROVED_FOR_TESTING.value:
        raise BoardError("a result can be recorded only on an entry that is being tested")
    if not result_text.strip():
        raise BoardError("a result needs its text")
    e.description = f"{e.description}\n\nResult: {result_text.strip()}"
    e.confidence = confidence.value
    e.version += 1
    _save_version(db, e, actor, "result recorded")
    audit.record(db, actor, "board.result_recorded", "board_entry", e.public_id, {"confidence": confidence.value})
    _publish_release(db, actor, e, f"{e.public_id} result recorded")
    return e


# --- transitions ---------------------------------------------------------------------------------------


def _get(db: Session, entry_id: int) -> BoardEntry:
    e = db.get(BoardEntry, entry_id)
    if e is None:
        raise BoardError("board entry not found")
    return e


def _required_permission(to: S) -> str:
    return {
        S.UNDER_INVESTIGATION: P.BOARD_INVESTIGATE,
        S.VERIFIED: P.BOARD_VERIFY,
        S.APPROVED_FOR_TESTING: P.BOARD_APPROVE_TESTING,
        S.ACTIVE_INSTRUCTION: P.BOARD_ACTIVATE,
        S.REJECTED: P.BOARD_RETIRE,
        S.EXPIRED: P.BOARD_RETIRE,
    }[to]


def transition(db: Session, actor: Actor, entry_id: int, to: BoardStatus, reason: str = "") -> dict[str, Any]:
    """Move an entry along the lifecycle. Returns ``{"entry", "approval_id"?}``."""
    e = _get(db, entry_id)
    cur = S(e.status)
    if to not in _FORWARD[cur]:
        raise BoardError(f"transition {cur.value} → {to.value} is not allowed")
    actor.require(_required_permission(to))
    if to in (S.REJECTED, S.EXPIRED, S.UNDER_INVESTIGATION) and not reason.strip():
        raise BoardError("a reason is required for this transition")

    if to is S.VERIFIED:
        _require_verified_evidence(db, actor, e)
    if to is S.APPROVED_FOR_TESTING:
        _require_test_plan(e)
        if RiskClass(e.risk_class) is not RiskClass.ROUTINE and not actor.is_owner:
            return _ask_owner(db, actor, e, to, reason)
    if to is S.ACTIVE_INSTRUCTION:
        _require_activatable(db, e, cur)
        if RiskClass(e.risk_class) is not RiskClass.ROUTINE and not actor.is_owner:
            return _ask_owner(db, actor, e, to, reason)

    _apply(db, actor, e, to, reason)
    return {"entry": e, "approval_id": None}


def _apply(db: Session, actor: Actor, e: BoardEntry, to: S, reason: str) -> None:
    before_active = e.status in ACTIVE_SET
    e.status = to.value
    e.status_reason = reason or None
    e.status_changed_at = utcnow()
    e.version += 1
    if to is S.UNDER_INVESTIGATION and actor.agent_id and e.responsible_agent_id is None:
        e.responsible_agent_id = actor.agent_id
    _save_version(db, e, actor, f"→ {to.value}" + (f": {reason}" if reason else ""))
    audit.record(db, actor, "board.transition", "board_entry", e.public_id, {"to": to.value, "reason": reason})
    after_active = to.value in ACTIVE_SET
    if before_active or after_active:
        _publish_release(db, actor, e, f"{e.public_id} → {to.value}")


def _require_verified_evidence(db: Session, actor: Actor, e: BoardEntry) -> None:
    items = evidence.for_board_entry(db, e.id)
    if not items:
        raise BoardError("cannot verify a finding that has no evidence")
    evidence.verify_all(db, items, actor)  # CEO re-checks; it does not trust earlier status
    if any(i.verification_status == "failed" for i in items):
        bad = [i.ref for i in items if i.verification_status == "failed"]
        raise BoardError(f"evidence failed verification: {bad}")
    if not any(i.verification_status == "verified" for i in items):
        raise BoardError("at least one machine-verifiable evidence item is required; notes and URLs alone are not enough")


def _require_test_plan(e: BoardEntry) -> None:
    plan = e.test_plan or {}
    missing = [k for k in ("hypothesis", "success_metric", "baseline", "observation_period") if not plan.get(k)]
    if missing:
        raise BoardError(f"an experiment needs: {', '.join(missing)}")


def _require_activatable(db: Session, e: BoardEntry, cur: S) -> None:
    cat = BoardCategory(e.category)
    if cat in CAUSAL_CATEGORIES and e.confidence == Confidence.LOW.value:
        raise BoardError(
            "low-confidence findings about audience behaviour cannot become rules; "
            "approve them as an experiment or gather more evidence"
        )
    if cur is S.APPROVED_FOR_TESTING:
        newer = [ev for ev in evidence.for_board_entry(db, e.id) if ev.created_at > e.status_changed_at]
        if not any(ev.verification_status != "failed" for ev in newer):
            raise BoardError("an experiment needs result evidence recorded after testing began")


def _ask_owner(db: Session, actor: Actor, e: BoardEntry, to: S, reason: str) -> dict[str, Any]:
    req = approvals.request(
        db, actor, kind=ApprovalKind.BOARD_ACTIVATION,
        title=f"{e.public_id}: {e.title}",
        summary=f"Move this {e.risk_class}-class lesson to {to.value}. {e.description[:400]}",
        why_it_matters=f"Risk class '{e.risk_class}' changes rules or strategy and needs your approval.",
        if_approved=f"The lesson becomes {to.value} and is sent to the relevant workers.",
        if_rejected="The lesson stays as it is and is not applied to production.",
        evidence=[{"kind": ev.kind, "ref": ev.ref, "status": ev.verification_status} for ev in evidence.for_board_entry(db, e.id)],
        channel_id=e.channel_id, subject_type="board_entry", subject_id=e.id,
        action={"handler": "board.transition", "args": {"entry_id": e.id, "to": to.value, "reason": reason}},
    )
    e.approval_request_id = req.id
    db.flush()
    return {"entry": e, "approval_id": req.id}


@approvals.register_handler("board.transition")
def _on_owner_approval(db: Session, owner: Actor, req, args: dict[str, Any]) -> dict[str, Any]:
    e = _get(db, args["entry_id"])
    to = S(args["to"])
    if to not in _FORWARD[S(e.status)]:
        raise BoardError(f"entry is now {e.status}; the approved transition no longer applies")
    if to is S.ACTIVE_INSTRUCTION:
        _require_activatable(db, e, S(e.status))
    _apply(db, owner, e, to, args.get("reason", ""))
    return {"entry": e.public_id, "status": e.status}


# --- reading for workers -----------------------------------------------------------------------------


def instructions_for(db: Session, channel_slug: str | None, department: str | None = None) -> dict[str, Any]:
    """The approved instructions and experiments that apply to one channel (and department)."""
    cid = _channel_id(db, channel_slug)
    entries = _active_entries(db, cid, department)
    return {
        "release_version": current_release_version(db),
        "scope": _scope(channel_slug, department),
        "digest": _digest(entries),
        "instructions": [serialize(db, e) for e in entries if e.status == S.ACTIVE_INSTRUCTION.value],
        "experiments": [serialize(db, e) for e in entries if e.status == S.APPROVED_FOR_TESTING.value],
    }


def _agent_department(db: Session, agent: Agent) -> str | None:
    return db.get(Department, agent.department_id).slug if agent.department_id else None


def acknowledge(db: Session, actor: Actor, channel_slug: str | None, task_id: int | None = None) -> dict[str, Any]:
    """An agent confirms it has loaded the current instructions for its department and channel."""
    if actor.agent_id is None:
        raise BoardError("only agents acknowledge board updates")
    agent = db.get(Agent, actor.agent_id)
    dep = _agent_department(db, agent)  # type: ignore[arg-type]
    info = instructions_for(db, channel_slug, dep)
    exists = db.scalar(
        select(BoardAcknowledgement).where(
            BoardAcknowledgement.agent_id == actor.agent_id, BoardAcknowledgement.scope == info["scope"],
            BoardAcknowledgement.digest == info["digest"],
        )
    )
    if exists is None:
        db.add(BoardAcknowledgement(
            agent_id=actor.agent_id, scope=info["scope"], release_version=info["release_version"],
            digest=info["digest"], task_id=task_id,
        ))
        db.flush()
        audit.record(db, actor, "board.acknowledged", "board_release", info["release_version"],
                     {"scope": info["scope"], "digest": info["digest"][:12]})
    return info


def assert_acknowledged(db: Session, agent_id: int, channel_slug: str | None) -> dict[str, Any]:
    agent = db.get(Agent, agent_id)
    dep = _agent_department(db, agent)  # type: ignore[arg-type]
    info = instructions_for(db, channel_slug, dep)
    if not info["instructions"] and not info["experiments"]:
        return info  # nothing to acknowledge
    ok = db.scalar(
        select(BoardAcknowledgement.id).where(
            BoardAcknowledgement.agent_id == agent_id, BoardAcknowledgement.scope == info["scope"],
            BoardAcknowledgement.digest == info["digest"],
        )
    )
    if ok is None:
        raise AcknowledgementRequired(info["scope"], info["digest"])
    return info


def pending_updates(db: Session, agent: Agent) -> list[dict[str, Any]]:
    """Channels where this agent has relevant, not-yet-acknowledged instructions."""
    out = []
    for ch in db.scalars(select(Channel)):
        try:
            assert_acknowledged(db, agent.id, ch.slug)
        except AcknowledgementRequired as e:
            out.append({"channel": ch.slug, "scope": e.scope})
    return out


# --- serialisation / poster ----------------------------------------------------------------------------


def serialize(db: Session, e: BoardEntry, with_evidence: bool = True) -> dict[str, Any]:
    ch = db.get(Channel, e.channel_id) if e.channel_id else None
    resp = db.get(Agent, e.responsible_agent_id) if e.responsible_agent_id else None
    out = {
        "id": e.id, "public_id": e.public_id, "title": e.title, "description": e.description,
        "category": e.category, "channel": ch.slug if ch else None, "status": e.status,
        "confidence": e.confidence, "risk_class": e.risk_class, "version": e.version,
        "responsible_manager": resp.name if resp else None,
        "review_date": e.review_date.isoformat() if e.review_date else None, "test_plan": e.test_plan,
        "status_reason": e.status_reason, "created_by": e.created_by, "created_at": iso(e.created_at),
        "updated_at": iso(e.updated_at), "status_changed_at": iso(e.status_changed_at),
        "approval_request_id": e.approval_request_id,
    }
    if with_evidence:
        out["evidence"] = [evidence.serialize(x) for x in evidence.for_board_entry(db, e.id)]
    return out


def poster(db: Session, *, owner_view: bool = True) -> dict[str, Any]:
    """Structured data behind the dashboard poster."""
    q = select(BoardEntry).order_by(BoardEntry.updated_at.desc())
    entries = list(db.scalars(q))
    if owner_view:
        entries = [e for e in entries if e.status in OWNER_VISIBLE]
    channels = {c.id: c for c in db.scalars(select(Channel))}
    sections: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for e in entries:
        key = channels[e.channel_id].slug if e.channel_id else "company"
        sections.setdefault(key, {}).setdefault(e.status, []).append(serialize(db, e))
    releases = list(db.scalars(select(BoardRelease).order_by(BoardRelease.version.desc()).limit(25)))
    return {
        "current_version": current_release_version(db),
        "sections": sections,
        "counts": {s.value: sum(1 for e in entries if e.status == s.value) for s in S},
        "releases": [
            {"version": r.version, "reason": r.reason, "created_by": r.created_by, "created_at": iso(r.created_at)}
            for r in releases
        ],
        "note": "This poster renders the structured board data; it is not a separate source of truth.",
    }


def entry_history(db: Session, entry_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(BoardEntryVersion).where(BoardEntryVersion.entry_id == entry_id).order_by(BoardEntryVersion.version)
    )
    return [
        {"version": r.version, "snapshot": r.snapshot, "changed_by": r.changed_by, "change_note": r.change_note,
         "created_at": iso(r.created_at)}
        for r in rows
    ]
