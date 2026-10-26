"""Owner-facing executive layer (Layer C): concise, verified, and honest about what is missing."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import views as analytics_views
from app.enums import ApprovalStatus, OperatingMode, OriginalityStatus, TaskStatus, VideoStage
from app.models.approvals import ApprovalRequest
from app.models.board import BoardEntry
from app.models.org import Agent, Channel
from app.models.tasks import Task
from app.models.videos import Video
from app.services import approvals, audit, board, finance, reports, tasks, videos
from app.services import settings as settings_svc
from app.timeutil import iso
from app.youtube import connection as conn_svc

_IN_PRODUCTION = {
    VideoStage.IDEA.value, VideoStage.RESEARCH.value, VideoStage.BRIEF.value, VideoStage.SCRIPT.value,
    VideoStage.PRODUCTION.value, VideoStage.QA.value, VideoStage.PACKAGING.value,
}
_RIGHTS_OPEN = {
    OriginalityStatus.NEEDS_REVISION.value, OriginalityStatus.BLOCKED.value, OriginalityStatus.HUMAN_REVIEW_REQUIRED.value,
}

UNAVAILABLE = {
    "youtube_analytics": {"available": False, "reason": "No YouTube channel is connected (Phase 4)."},
    "audience_feedback": {"available": False, "reason": "Comments are not retrieved until YouTube is connected."},
    "revenue": {"available": False, "reason": "No authorised revenue source is connected."},
}


def live_risks(db: Session) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    chain = audit.verify_chain(db)
    if not chain["ok"]:
        out.append({"severity": "critical", "text": f"Audit log integrity check failed at event {chain['first_bad_id']}."})
    if settings_svc.is_emergency_paused(db):
        out.append({"severity": "high", "text": "Emergency pause is active."})
    n_blocked = db.scalar(select(func.count()).select_from(Task).where(Task.status == TaskStatus.BLOCKED.value)) or 0
    if n_blocked:
        out.append({"severity": "medium", "text": f"{n_blocked} task(s) are blocked."})
    n_failed = db.scalar(select(func.count()).select_from(Task).where(Task.status == TaskStatus.FAILED.value)) or 0
    if n_failed:
        out.append({"severity": "medium", "text": f"{n_failed} task(s) have failed."})
    n_rights = db.scalar(select(func.count()).select_from(Video).where(Video.originality_status.in_(_RIGHTS_OPEN))) or 0
    if n_rights:
        out.append({"severity": "high", "text": f"{n_rights} video(s) have unresolved originality or rights concerns."})
    return out


def executive_overview(db: Session) -> dict[str, Any]:
    mode = settings_svc.operating_mode(db)
    pause = settings_svc.emergency_pause_state(db)
    latest = reports.latest_executive(db)
    all_videos = list(db.scalars(select(Video)))
    pending = approvals.pending(db)
    blocked = list(db.scalars(select(Task).where(Task.status == TaskStatus.BLOCKED.value).order_by(Task.updated_at.desc()).limit(5)))
    improvements = list(db.scalars(
        select(BoardEntry).where(BoardEntry.status.in_(["VERIFIED", "APPROVED_FOR_TESTING", "ACTIVE_INSTRUCTION"]))
        .order_by(BoardEntry.status_changed_at.desc()).limit(5)
    ))
    return {
        "company_status": {
            "operating_mode": mode.value,
            "emergency_pause": bool(pause.get("active")),
            "pause_detail": pause if pause.get("active") else None,
            "label": "PAUSED" if pause.get("active") else mode.value,
            "real_publishing_enabled": mode in (OperatingMode.APPROVAL_FIRST, OperatingMode.CONTROLLED_AUTONOMY)
            and not pause.get("active"),
        },
        "latest_executive_report": None if latest is None else {
            "id": latest.id, "created_at": iso(latest.created_at), "headline": latest.summary_text,
            "period": latest.content.get("period"), "check_summary": latest.content.get("check_summary"),
            "next_priorities": latest.content.get("next_priorities", []),
        },
        "executive_report_note": None if latest else "Nothing has been verified yet. Generate the first report to see what actually happened.",
        "pipeline": {
            "in_production": sum(1 for v in all_videos if v.stage in _IN_PRODUCTION),
            "awaiting_owner_approval": sum(1 for v in all_videos if v.owner_approval_status == "PENDING"),
            "cleared_for_publishing": sum(1 for v in all_videos if v.stage in (VideoStage.READY.value, VideoStage.SCHEDULED.value)),
            "published": sum(1 for v in all_videos if v.stage == VideoStage.PUBLISHED.value),
            "blocked_by_rights": sum(1 for v in all_videos if v.originality_status in _RIGHTS_OPEN),
            "total": len(all_videos),
        },
        "blocked_tasks": [tasks.summary(db, t) for t in blocked],
        "approvals_pending": {"count": len(pending), "items": [approvals.serialize(a) for a in pending[:5]]},
        "risks": live_risks(db),
        "costs": finance.spend_summary(db),
        "recent_verified_improvements": [
            {"public_id": e.public_id, "title": e.title, "status": e.status, "channel_id": e.channel_id} for e in improvements
        ],
        "metrics_availability": UNAVAILABLE,
        "labels": {"actual": "Measured from records or provider responses", "estimate": "Calculated, not charged",
                   "unavailable": "No data source connected; shown as unavailable, never as zero"},
    }


def channel_dashboard(db: Session, slug: str) -> dict[str, Any]:
    ch = db.scalar(select(Channel).where(Channel.slug == slug))
    if ch is None:
        raise LookupError(slug)
    vids = list(db.scalars(select(Video).where(Video.channel_id == ch.id).order_by(Video.id.desc())))
    chan_tasks = list(db.scalars(select(Task).where(Task.channel_id == ch.id).order_by(Task.id.desc()).limit(30)))
    conn = conn_svc.connection_for(db, ch.id)
    instr = board.instructions_for(db, slug)
    entries = [board.serialize(db, e) for e in db.scalars(
        select(BoardEntry).where(BoardEntry.channel_id == ch.id, BoardEntry.status.in_(sorted(board.OWNER_VISIBLE)))
        .order_by(BoardEntry.updated_at.desc()).limit(20))]
    return {
        "channel": {"slug": ch.slug, "name": ch.name, "kind": ch.kind, "objective": ch.objective, "brand": ch.brand,
                    "policy": ch.policy, "youtube_connected": bool(conn and conn.status == "CONNECTED")},
        "performance": analytics_views.channel_performance(db, ch),
        "audience_feedback": analytics_views.channel_feedback(db, ch),
        "production": [videos.serialize(db, v) for v in vids if v.stage != VideoStage.PUBLISHED.value],
        "publishing_history": [videos.serialize(db, v) for v in vids if v.stage == VideoStage.PUBLISHED.value],
        "tasks": [tasks.summary(db, t) for t in chan_tasks],
        "board": {"release_version": instr["release_version"], "active_instructions": instr["instructions"],
                  "experiments": instr["experiments"], "lessons": entries},
        "recommendations": analytics_views.channel_recommendations(db, ch),
    }


def rights_dashboard(db: Session) -> dict[str, Any]:
    vids = list(db.scalars(select(Video).order_by(Video.id.desc())))
    ser = [videos.serialize(db, v) for v in vids]
    return {
        "cleared": [v for v in ser if v["originality_status"] == "PASS" and v["ceo_rights_verified"]],
        "awaiting_review": [v for v in ser if v["originality_status"] == "NOT_REVIEWED"],
        "blocked_or_open": [v for v in ser if v["originality_status"] in _RIGHTS_OPEN],
        "missing_licenses": [v for v in ser if v["asset_license_issues"] > 0],
        "medical_review_pending": [v for v in ser if v["medical_review_status"] in ("PENDING", "REJECTED")],
        "note": "Similarity checks are screening aids and never prove a video is free of copyright risk.",
    }


def approvals_view(db: Session, include_decided: bool = True) -> dict[str, Any]:
    pend = approvals.pending(db)
    decided = list(db.scalars(
        select(ApprovalRequest).where(ApprovalRequest.status != ApprovalStatus.PENDING.value)
        .order_by(ApprovalRequest.decided_at.desc().nullslast(), ApprovalRequest.id.desc()).limit(20)
    )) if include_decided else []
    return {"pending": [approvals.serialize(a) for a in pend], "recent": [approvals.serialize(a) for a in decided]}


def organization_view(db: Session) -> dict[str, Any]:
    """Chart, responsibilities and *current* work with real statuses (no raw outputs)."""
    from app.services import org

    tree = org.organization_tree(db)
    agents = {a.id: a.slug for a in db.scalars(select(Agent))}
    open_statuses = [s.value for s in TaskStatus if s not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)]
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for t in db.scalars(select(Task).where(Task.status.in_(open_statuses)).order_by(Task.priority, Task.id)):
        key = agents.get(t.assigned_agent_id, "__system__") if t.assigned_agent_id else "__system__"
        by_agent.setdefault(key, []).append(tasks.summary(db, t))
    counts = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
    for a in tree["agents"]:
        a["open_tasks"] = by_agent.get(a["slug"], [])
        a["actively_running"] = any(t["status"] == "RUNNING" for t in a["open_tasks"])
    return {**tree, "system_tasks": by_agent.get("__system__", []), "task_counts": counts,
            "note": "An agent is shown as active only while one of its tasks is genuinely RUNNING."}
