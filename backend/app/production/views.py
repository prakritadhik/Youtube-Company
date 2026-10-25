"""Owner-facing production views (Layer C). Raw similarity comparisons and tool output stay in diagnostics."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.org import Channel
from app.models.production import CreativeBrief, OriginalityReview, QAReport, ScriptVersion, SourceRecord
from app.models.tasks import Task
from app.models.videos import Video
from app.production import assets as assets_svc
from app.services import gate, tasks, videos
from app.timeutil import iso


def _brief(db: Session, video_id: int) -> dict[str, Any] | None:
    b = db.scalar(select(CreativeBrief).where(CreativeBrief.video_id == video_id).order_by(CreativeBrief.version.desc()))
    if b is None:
        return None
    return {"version": b.version, "status": b.status, "created_by": b.created_by, "reviewed_by": b.reviewed_by,
            "review_notes": b.review_notes, "board_version_used": b.board_version_used,
            "content": {k: v for k, v in b.content.items() if not k.startswith("_")}}


def _text(db: Session, video_id: int) -> dict[str, Any] | None:
    s = db.scalar(select(ScriptVersion).where(ScriptVersion.video_id == video_id, ScriptVersion.kind.in_(["lyrics", "script"]))
                  .order_by(ScriptVersion.created_at.desc(), ScriptVersion.id.desc()))
    if s is None:
        return None
    return {"kind": s.kind, "version": s.version, "author_kind": s.author_kind, "author": s.author_label, "text": s.text,
            "cited_claims": len(s.claims), "created_at": iso(s.created_at)}


def _rights(db: Session, video_id: int) -> dict[str, Any] | None:
    r = db.scalar(select(OriginalityReview).where(OriginalityReview.video_id == video_id).order_by(OriginalityReview.version.desc()))
    if r is None:
        return None
    rep = r.report
    return {
        "status": r.status, "version": r.version, "created_at": iso(r.created_at), "reviewed_by": r.created_by,
        "findings": [{"severity": f["severity"], "code": f["code"], "message": f["message"]} for f in rep["findings"]],
        "corrective_actions": rep["required_corrective_actions"], "distinctive_contributions": rep["distinctive_creative_contributions"],
        "tools_used": rep["tools_used"], "limitations": rep["limitations"], "status_basis": rep["status_basis"],
        "script_author": rep["script_or_lyric_originality"].get("author"), "script_author_kind": rep["script_or_lyric_originality"].get("author_kind"),
    }


def _qa(db: Session, video_id: int) -> dict[str, Any] | None:
    q = db.scalar(select(QAReport).where(QAReport.video_id == video_id).order_by(QAReport.version.desc()))
    if q is None:
        return None
    return {"passed": q.passed, "version": q.version, "created_at": iso(q.created_at),
            "checks": [{"id": c["id"], "name": c["name"], "status": c["status"], "detail": c["detail"], "correction": c.get("correction", "")}
                       for c in q.checks]}


def video_detail(db: Session, video_id: int) -> dict[str, Any] | None:
    v = db.get(Video, video_id)
    if v is None:
        return None
    ch = db.get(Channel, v.channel_id)
    all_tasks = db.scalars(select(Task).where(Task.video_id == video_id).order_by(Task.id)).all()
    return {
        **videos.serialize(db, v),
        "channel_name": ch.name if ch else None, "channel_policy": ch.policy if ch else {},
        "has_video_file": bool(v.final_video_path), "has_thumbnail": bool(v.thumbnail_path),
        "description": (v.metadata_json or {}).get("description"), "tags": (v.metadata_json or {}).get("tags", []),
        "brief": _brief(db, video_id), "text": _text(db, video_id),
        "sources": [{"id": s.id, "url": s.url, "title": s.title, "publisher": s.publisher, "retrieved_at": iso(s.retrieved_at),
                     "supports": s.supports} for s in db.scalars(select(SourceRecord).where(SourceRecord.video_id == video_id))],
        "assets": [_asset_row(a) for a in assets_svc.for_video(db, video_id)],
        "rights": _rights(db, video_id), "qa": _qa(db, video_id),
        "gate_for_publishing": gate.evaluate(db, v, purpose="publish").as_dict(),
        "tasks": [tasks.summary(db, t) for t in all_tasks],
        "honesty": ("The soundtrack is an algorithmic instrumental with no vocals. " if any(a.kind == "music" and a.provider == "local-procedural" for a in assets_svc.for_video(db, video_id)) else "")
        + "Similarity checks are screening aids, not proof of copyright safety.",
    }


def _asset_row(a) -> dict[str, Any]:
    d = assets_svc.serialize(a)
    return {"id": d["id"], "kind": d["kind"], "role": d["role"], "provider": d["provider"], "model": d["model"], "seed": d["seed"],
            "license_type": d["license_type"], "license_ref": d["license_ref"], "commercial_use": d["commercial_use"],
            "attribution": d["attribution"], "simulated": d["simulated"], "problems": d["problems"], "size_bytes": d["size_bytes"]}
