"""Alerts: the short list of things the owner should know about.

An alert has a *key*. Raising the same key again while it is open bumps its count and last-seen time instead of making a new
row, so a problem that persists for a week is one alert, not a thousand. Resolving is done by the check that raised it once the
condition is gone; the owner can acknowledge (I have seen it) but cannot make a real problem disappear.

There is no e-mail or phone notification: none is configured, and pretending otherwise would be worse than nothing. Alerts appear
in the dashboard (a badge on every page and the Operations page) and in ``python -m app.cli ops-status``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ops import Alert
from app.security.permissions import Actor
from app.services import audit
from app.timeutil import iso, utcnow

SEVERITIES = ("info", "warning", "critical")
ORDER = {"critical": 0, "warning": 1, "info": 2}


def open_for(db: Session, key: str) -> Alert | None:
    return db.scalar(select(Alert).where(Alert.key == key, Alert.status.in_(("OPEN", "ACKNOWLEDGED"))))


def raise_alert(db: Session, key: str, severity: str, title: str, detail: str = "", *, source: str = "system", related: dict[str, Any] | None = None) -> Alert:
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    a = open_for(db, key)
    now = utcnow()
    if a is not None:
        a.count += 1
        a.last_seen, a.detail, a.related = now, detail, related or a.related
        if ORDER[severity] < ORDER[a.severity]:  # it got worse: say so, and ask again even if it had been acknowledged
            a.severity, a.status, a.acknowledged_by, a.acknowledged_at = severity, "OPEN", None, None
        a.title = title
        return a
    a = Alert(key=key, severity=severity, title=title[:200], detail=detail, source=source, related=related or {}, first_seen=now, last_seen=now)
    db.add(a)
    db.flush()
    audit.record(db, Actor.system("alerts"), "alert.raised", "alert", a.id, {"key": key, "severity": severity, "title": title[:200]})
    return a


def resolve(db: Session, key: str, note: str = "") -> bool:
    a = open_for(db, key)
    if a is None:
        return False
    a.status, a.resolved_at = "RESOLVED", utcnow()
    audit.record(db, Actor.system("alerts"), "alert.resolved", "alert", a.id, {"key": key, "note": note})
    return True


def acknowledge(db: Session, actor: Actor, alert_id: int) -> Alert:
    if not actor.is_owner:
        raise PermissionError("only the owner can acknowledge an alert")
    a = db.get(Alert, alert_id)
    if a is None or a.status == "RESOLVED":
        raise LookupError("no open alert with that id")
    a.status, a.acknowledged_by, a.acknowledged_at = "ACKNOWLEDGED", actor.label, utcnow()
    audit.record(db, actor, "alert.acknowledged", "alert", a.id, {"key": a.key})
    return a


def list_alerts(db: Session, *, include_resolved: bool = False, limit: int = 100) -> list[Alert]:
    q = select(Alert).order_by(Alert.last_seen.desc()).limit(limit)
    if not include_resolved:
        q = q.where(Alert.status != "RESOLVED")
    rows = list(db.scalars(q))
    rows.sort(key=lambda a: (a.status == "ACKNOWLEDGED", ORDER[a.severity], -a.last_seen.timestamp()))
    return rows


def counts(db: Session) -> dict[str, int]:
    out = {s: 0 for s in SEVERITIES}
    for sev, n in db.execute(select(Alert.severity, func.count()).where(Alert.status == "OPEN").group_by(Alert.severity)):
        out[sev] = n
    out["open"] = sum(out[s] for s in SEVERITIES)
    return out


def serialize(a: Alert) -> dict[str, Any]:
    return {"id": a.id, "key": a.key, "severity": a.severity, "title": a.title, "detail": a.detail, "source": a.source, "status": a.status, "count": a.count,
            "first_seen": iso(a.first_seen), "last_seen": iso(a.last_seen), "acknowledged_by": a.acknowledged_by,
            "acknowledged_at": iso(a.acknowledged_at) if a.acknowledged_at else None, "related": a.related}
