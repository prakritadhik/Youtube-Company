"""The daily operating cycle and the health-check task.

The cycle follows the order the company works in (review, research, plan, produce, check quality, publish, monitor, learn,
report). Each phase records what it *actually did*; a phase with nothing to do says so, and a phase that depends on something not
built or not connected says "unavailable" with the reason. A quiet day produces a quiet report: nothing is invented.

What the cycle may do by itself is deliberately small: re-assess open ideas, list what needs a person, and (only in Controlled
autonomy, only for channels the owner listed, only inside the owner's daily limit, only if every publication gate condition holds)
queue an upload. It cannot approve, publish medical content, spend money, or bypass a gate.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from app.db import session_scope
from app.enums import OperatingMode, TaskStatus, VideoStage
from app.models.org import Channel
from app.models.research import ContentIdea
from app.models.tasks import Task
from app.models.videos import Video
from app.models.youtube import YouTubeUpload
from app.ops import alerts, health, schedule
from app.research import ideas
from app.security.permissions import Actor
from app.services import gate, reports
from app.services import settings as settings_svc
from app.storage import write_json_artifact
from app.timeutil import iso, utcnow
from app.worker.runner import HandlerResult, TaskContext, handler
from app.youtube import connection as conn_svc
from app.youtube import quota, upload

STALE_ASSESSMENT = timedelta(hours=24)
MAX_REASSESS = 25


@handler("ops.health_check")
def health_check(ctx: TaskContext) -> HandlerResult:
    with session_scope() as db:
        checks = health.run_checks(db)
        changes = health.apply(db, checks)
    report = {"checked_at": iso(utcnow()), "checks": checks, "alerts": changes,
              "summary": {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "warn", "fail")}}
    ref = write_json_artifact("ops", f"health-{ctx.task_id}.json", report)
    return HandlerResult(result={"summary": report["summary"], "checked_at": report["checked_at"], "checks": checks, **changes},
                         evidence=[{"kind": "file", "ref": ref, "note": "health report"}])


def _phase(name: str, status: str, summary: str, **detail: Any) -> dict[str, Any]:
    return {"phase": name, "status": status, "summary": summary, **detail}  # status: done | nothing_to_do | unavailable | skipped


def run_cycle(db) -> dict[str, Any]:
    now = utcnow()
    actor = Actor.system("daily-cycle")
    phases: list[dict[str, Any]] = []

    # A. review
    by_status = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
    by_stage = dict(db.execute(select(Video.stage, func.count()).group_by(Video.stage)).all())
    phases.append(_phase("review", "done", "Counted the company's current state from its records.", tasks_by_status=by_status, videos_by_stage=by_stage,
                         open_alerts=alerts.counts(db)))

    # B. research: keep open ideas honest (an assessment that is a day old may no longer be true)
    stale = list(db.scalars(select(ContentIdea).where(ContentIdea.status.in_(("PROPOSED", "ASSESSED", "APPROVED", "PARKED"))).order_by(ContentIdea.id)))
    stale = [i for i in stale if i.assessed_at is None or now - i.assessed_at > STALE_ASSESSMENT][:MAX_REASSESS]
    unblocked = []
    for i in stale:
        before = (i.assessment or {}).get("feasibility", {}).get("verdict")
        a = ideas._store_assessment(db, actor, i)
        if i.status == "PARKED" and before == "needs_provider" and a["feasibility"]["verdict"] == "ready":
            unblocked.append({"idea": i.id, "title": i.title})
    phases.append(_phase("research", "done" if stale else "nothing_to_do", f"Re-assessed {len(stale)} idea(s) whose assessment was missing or over a day old." if stale else "No idea needed re-assessing.",
                         reassessed=[i.id for i in stale], newly_producible=unblocked))

    # C. planning
    plan = ideas.plan(db)
    needs_decision = [{"idea": it["id"], "title": it["title"], "channel": ch["channel"]} for ch in plan for it in ch["items"] if it["status"] == "ASSESSED"]
    approved = [{"idea": it["id"], "title": it["title"], "channel": ch["channel"]} for ch in plan for it in ch["items"] if it["status"] == "APPROVED"]
    phases.append(_phase("planning", "done" if (needs_decision or approved) else "nothing_to_do",
                         f"{len(needs_decision)} idea(s) wait for a decision; {len(approved)} approved idea(s) wait to start production." if (needs_decision or approved) else "The content plan is empty.",
                         needs_decision=needs_decision, approved_not_started=approved))

    # D. production: what is waiting for a person or is stuck
    waiting = db.scalars(select(Task).where(Task.task_type.like("manual.%"), Task.status.in_((TaskStatus.QUEUED.value, TaskStatus.CREATED.value)))).all()
    blocked = db.scalars(select(Task).where(Task.status == TaskStatus.BLOCKED.value)).all()
    phases.append(_phase("production", "done" if (waiting or blocked) else "nothing_to_do",
                         f"{len(waiting)} manual step(s) are waiting for a person; {len(blocked)} task(s) are blocked." if (waiting or blocked) else "Nothing is waiting in production.",
                         waiting_for_a_person=[{"task": t.id, "title": t.title, "age_hours": round((now - t.created_at).total_seconds() / 3600, 1)} for t in waiting[:30]],
                         blocked=[{"task": t.id, "title": t.title, "reason": t.blocked_reason} for t in blocked[:30]]))

    # E. quality
    corrections = db.scalars(select(Task).where(Task.task_type == "manual.correction", Task.status.notin_((TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)))).all()
    phases.append(_phase("quality", "done" if corrections else "nothing_to_do", f"{len(corrections)} correction task(s) are open after a failed check." if corrections else "No failed check is waiting for a correction.",
                         open_corrections=[{"task": t.id, "title": t.title} for t in corrections[:30]]))

    # F. publishing
    phases.append(_publishing(db, actor, now))

    # G / H. not available yet
    phases.append(_phase("monitoring", "unavailable", "Performance, retention and comment data are unavailable: YouTube analytics are not connected."))
    phases.append(_phase("learning", "unavailable", "No experiment or verified performance lesson exists to review yet."))

    # I. the executive report, built and independently verified like any other
    rep = reports.run_reporting_cycle(db)
    phases.append(_phase("report", "done", "Built the department reports, the COO's summary and the CEO's verified executive report.", executive_report_id=rep.id))

    needs_you = len(needs_decision) + len(approved) + len(waiting) + len(blocked) + len(corrections)
    quiet = needs_you == 0 and not stale
    if needs_you:
        alerts.raise_alert(db, "cycle:needs-you", "info", f"{needs_you} item(s) need your attention", "See the latest daily cycle on the Operations page.", source="daily-cycle")
    else:
        alerts.resolve(db, "cycle:needs-you", "nothing waiting")
    return {"ran_at": iso(now), "timezone": str(schedule.zone(db)), "quiet": quiet, "needs_attention": needs_you, "phases": phases}


def _publishing(db, actor: Actor, now) -> dict[str, Any]:
    ready = list(db.scalars(select(Video).where(Video.stage == VideoStage.READY.value, Video.youtube_video_id.is_(None)).order_by(Video.id)))
    mode = settings_svc.operating_mode(db)
    autonomy = settings_svc.get(db, "autonomy")
    if not ready:
        return _phase("publishing", "nothing_to_do", "No cleared video is waiting to go out.")
    queued: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    if mode is not OperatingMode.CONTROLLED_AUTONOMY:
        held = [{"video": v.id, "title": v.title, "why": "needs your approval and the operating mode is not Controlled autonomy" if mode is OperatingMode.APPROVAL_FIRST
                 else f"operating mode is {mode.value}"} for v in ready]
        return _phase("publishing", "done", f"{len(ready)} cleared video(s) wait for you; nothing was queued because the mode is {mode.value}.", queued=queued, held=held)
    tz = schedule.zone(db)
    day_start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    limit = int(autonomy.get("max_publishes_per_day_per_channel", 0))
    for v in ready:
        ch = db.get(Channel, v.channel_id)
        if ch.slug not in autonomy.get("autopublish_channels", []):  # type: ignore[union-attr]
            held.append({"video": v.id, "title": v.title, "why": "this channel is not on your autopublish list"})
            continue
        today = db.scalar(select(func.count()).select_from(YouTubeUpload).join(Video, Video.id == YouTubeUpload.video_id)
                          .where(Video.channel_id == v.channel_id, YouTubeUpload.created_at >= day_start.astimezone(now.tzinfo))) or 0
        if today >= limit:
            held.append({"video": v.id, "title": v.title, "why": f"today's limit of {limit} for this channel is used"})
            continue
        try:
            up = upload.prepare(db, actor, v.id, privacy=str(autonomy.get("default_privacy", "private")))
            queued.append({"video": v.id, "title": v.title, "upload": up.id, "privacy": up.privacy_requested})
        except (gate.GateBlocked, upload.UploadError, quota.QuotaExceeded, conn_svc.YouTubeError) as e:
            why = ", ".join(getattr(getattr(e, "result", None), "codes", lambda: [])()) or str(e)
            held.append({"video": v.id, "title": v.title, "why": why[:300]})
    return _phase("publishing", "done", f"Queued {len(queued)} upload(s); {len(held)} video(s) held back.", queued=queued, held=held)


@handler("ops.daily_cycle")
def daily_cycle(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    with session_scope() as db:
        result = run_cycle(db)
    ref = write_json_artifact("ops", f"daily-cycle-{ctx.task_id}.json", result)
    rep = next((p["executive_report_id"] for p in result["phases"] if p["phase"] == "report"), None)
    evidence = [{"kind": "file", "ref": ref, "note": "what each phase of the daily cycle did"}]
    if rep:
        evidence.append({"kind": "db_record", "ref": f"reports:{rep}", "note": "verified executive report"})
    return HandlerResult(result={"quiet": result["quiet"], "needs_attention": result["needs_attention"], "executive_report_id": rep, "ran_at": result["ran_at"],
                                 "phases": {p["phase"]: p["status"] for p in result["phases"]}, "report_ref": ref}, evidence=evidence)
