"""Tamper-evident audit log: each record hashes the previous record's hash and its own payload."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.security.permissions import Actor
from app.security.redaction import redact
from app.timeutil import utcnow

GENESIS_HASH = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(payload)).encode()).hexdigest()


def record(
    db: Session,
    actor: Actor,
    action: str,
    entity_type: str | None = None,
    entity_id: Any = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event. Must be called from a write session so the chain stays linear."""
    last = db.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    prev = last.hash if last else GENESIS_HASH
    ts = utcnow()
    clean = json.loads(_canonical(redact(details or {})))  # JSON round-trip == what is stored
    payload = {
        "ts": ts.isoformat(),
        "actor_kind": actor.kind.value,
        "actor_id": actor.id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": None if entity_id is None else str(entity_id),
        "details": clean,
    }
    event = AuditEvent(
        ts=ts,
        actor_kind=actor.kind.value,
        actor_id=actor.id,
        action=action,
        entity_type=entity_type,
        entity_id=payload["entity_id"],
        details=clean,
        prev_hash=prev,
        hash=_digest(prev, payload),
    )
    db.add(event)
    db.flush()
    return event


def verify_chain(db: Session) -> dict[str, Any]:
    """Recompute every hash from the stored columns. Returns ``{"ok", "checked", "first_bad_id"}``.

    Reads raw column values (not ORM objects) so that a stale session cache can never mask an edit
    made directly in the database.
    """
    prev = GENESIS_HASH
    checked = 0
    cols = (
        AuditEvent.id, AuditEvent.ts, AuditEvent.actor_kind, AuditEvent.actor_id, AuditEvent.action,
        AuditEvent.entity_type, AuditEvent.entity_id, AuditEvent.details, AuditEvent.prev_hash, AuditEvent.hash,
    )
    for row in db.execute(select(*cols).order_by(AuditEvent.id).execution_options(populate_existing=True)):
        payload = {
            "ts": row.ts.isoformat(), "actor_kind": row.actor_kind, "actor_id": row.actor_id,
            "action": row.action, "entity_type": row.entity_type, "entity_id": row.entity_id,
            "details": row.details,
        }
        if row.prev_hash != prev or row.hash != _digest(prev, payload):
            return {"ok": False, "checked": checked, "first_bad_id": row.id}
        prev = row.hash
        checked += 1
    return {"ok": True, "checked": checked, "first_bad_id": None}
