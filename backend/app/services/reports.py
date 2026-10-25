"""Reporting chain: department report → COO operations summary → CEO verified executive report.

Every material statement is a *claim* with a machine-checkable descriptor. The CEO step re-runs each
check against the database or filesystem instead of trusting the number a manager wrote down.

The generation here is deterministic (no language model). Later phases may add narrative drafting,
but narrative can only cite claims that survive this verification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select, true
from sqlalchemy.orm import Session

from app.enums import ApprovalStatus, ClaimClass, ReportKind, TaskStatus
from app.models.approvals import ApprovalRequest
from app.models.board import BoardEntry
from app.models.community import Comment, Metric, MetricGap, ReplyDraft
from app.models.finance import UsageRecord
from app.models.improvement import Experiment
from app.models.org import Agent, Department
from app.models.reports import Report
from app.models.research import ContentIdea, MetadataSet, ResearchNote, ThumbnailConcept
from app.models.tasks import Evidence, Task
from app.models.videos import Video
from app.models.youtube import YouTubeConnection
from app.security.permissions import Actor, P, PermissionDenied
from app.services import audit, board, evidence, finance, org
from app.services import settings as settings_svc
from app.timeutil import iso, utcnow

METHOD = "deterministic-v1 (no language model used)"


class ReportError(Exception):
    pass


# --- claims and checks -------------------------------------------------------------------------------------

_ENTITIES: dict[str, tuple[Any, str]] = {  # entity → (model, default time column)
    "tasks": (Task, "created_at"),
    "videos": (Video, "created_at"),
    "approval_requests": (ApprovalRequest, "created_at"),
    "board_entries": (BoardEntry, "created_at"),
    "usage_records": (UsageRecord, "ts"),
    "reports": (Report, "created_at"),
    "evidence": (Evidence, "created_at"),
    "content_ideas": (ContentIdea, "created_at"),
    "research_notes": (ResearchNote, "created_at"),
    "metadata_sets": (MetadataSet, "created_at"),
    "thumbnail_concepts": (ThumbnailConcept, "created_at"),
    "youtube_connections": (YouTubeConnection, "created_at"),
    "metrics": (Metric, "retrieved_at"),
    "metric_gaps": (MetricGap, "last_attempt"),
    "comments": (Comment, "retrieved_at"),
    "reply_drafts": (ReplyDraft, "created_at"),
    "experiments": (Experiment, "created_at"),
}


@dataclass
class Claim:
    text: str
    cls: ClaimClass
    value: Any = None
    check: dict[str, Any] | None = None
    source: str = ""
    reporter: str = ""

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["cls"] = self.cls.value
        return d


@dataclass
class CheckResult:
    status: str  # confirmed | contradicted | unverifiable
    actual: Any = None
    detail: str = ""


def connected_sources(db: Session) -> set[str]:
    """External data sources that are actually connected. ``youtube`` means at least one channel is connected and confirmed;
    revenue, keyword and trend sources are never connected, so statements that they are unavailable stay true."""
    out: set[str] = set()
    if db.scalar(select(func.count()).select_from(YouTubeConnection).where(YouTubeConnection.status == "CONNECTED")):
        out.add("youtube")
    return out


def _filters(model: Any, where: dict[str, Any], between: dict[str, Any] | None, default_time: str) -> list[Any]:
    cols = model.__table__.columns
    conds = []
    for key, val in (where or {}).items():
        if key not in cols or str(cols[key].type).startswith("JSON"):
            raise ReportError(f"column '{key}' cannot be used in a check")
        conds.append(cols[key].in_(val) if isinstance(val, list) else cols[key] == val)
    if between:
        col = between.get("column", default_time)
        if col not in cols:
            raise ReportError(f"column '{col}' cannot be used in a check")
        conds.append(cols[col] >= datetime.fromisoformat(between["start"]))
        conds.append(cols[col] < datetime.fromisoformat(between["end"]))
    return conds


def run_check(db: Session, check: dict[str, Any] | None, verifier: Actor) -> CheckResult:
    if not check:
        return CheckResult("unverifiable", None, "no machine-checkable evidence was supplied")
    kind = check.get("type")
    try:
        if kind in ("count", "sum"):
            model, default_time = _ENTITIES[check["entity"]]
            conds = _filters(model, check.get("where", {}), check.get("between"), default_time)
            if kind == "count":
                actual: Any = db.scalar(select(func.count()).select_from(model).where(and_(true(), *conds))) or 0
            else:
                col = model.__table__.columns[check["column"]]
                actual = int(db.scalar(select(func.coalesce(func.sum(col), 0)).where(and_(true(), *conds))) or 0)
            ok = actual == check["expected"]
            return CheckResult("confirmed" if ok else "contradicted", actual,
                               "matches the database" if ok else f"database says {actual}")
        if kind == "record":
            model, _ = _ENTITIES[check["entity"]]
            row = db.get(model, check["id"])
            if row is None:
                return CheckResult("contradicted", None, "record does not exist")
            for k, v in (check.get("where") or {}).items():
                if getattr(row, k, object()) != v:
                    return CheckResult("contradicted", getattr(row, k, None), f"field '{k}' differs")
            return CheckResult("confirmed", None, "record exists and matches")
        if kind == "evidence":
            ev = db.get(Evidence, check["id"])
            if ev is None:
                return CheckResult("contradicted", None, "evidence record does not exist")
            evidence.verify(db, ev, verifier)
            if ev.verification_status == "verified":
                return CheckResult("confirmed", None, ev.verification_detail or "")
            if ev.verification_status == "failed":
                return CheckResult("contradicted", None, ev.verification_detail or "")
            return CheckResult("unverifiable", None, ev.verification_detail or "")
        if kind == "source_not_connected":  # supports a claim that data is unavailable
            connected = check["source"] in connected_sources(db)
            return CheckResult("contradicted" if connected else "confirmed", None,
                               f"{check['source']} is {'connected' if connected else 'not connected'}")
    except (KeyError, ReportError) as e:
        return CheckResult("unverifiable", None, f"invalid check descriptor: {e}")
    return CheckResult("unverifiable", None, f"unknown check type '{kind}'")


def _sig(check: dict[str, Any] | None) -> str | None:
    if not check:
        return None
    return repr(sorted((k, repr(v)) for k, v in check.items() if k != "expected"))


# --- department report ------------------------------------------------------------------------------------


def _manager_of(db: Session, dept: Department) -> Agent | None:
    return db.scalar(select(Agent).where(Agent.department_id == dept.id, Agent.level == "manager"))


def build_department_report(
    db: Session, actor: Actor, dept_slug: str, start: datetime, end: datetime,
    extra_claims: list[Claim] | None = None,
) -> Report:
    actor.require(P.REPORT_SUBMIT)
    dept = db.scalar(select(Department).where(Department.slug == dept_slug))
    if dept is None:
        raise ReportError(f"unknown department '{dept_slug}'")
    mgr = _manager_of(db, dept)
    if actor.kind.value == "agent" and mgr is not None and actor.agent_id != mgr.id and actor.role not in ("coo", "ceo"):
        raise PermissionDenied(actor, "report:submit (only the department's own manager)")
    window = {"start": start.isoformat(), "end": end.isoformat()}
    who = mgr.name if mgr else "Department manager"

    def count(where: dict[str, Any], col: str | None = "created_at", windowed: bool = True) -> tuple[int, dict[str, Any]]:
        where = {"department_id": dept.id, **where}
        chk: dict[str, Any] = {"type": "count", "entity": "tasks", "where": where}
        if windowed:
            chk["between"] = {"column": col, **window}
        n = run_check(db, {**chk, "expected": -1}, actor).actual  # compute actual value
        chk["expected"] = n
        return n, chk

    done_n, done_chk = count({"status": TaskStatus.COMPLETED.value}, "completed_at")
    created_n, created_chk = count({})
    failed_n, failed_chk = count({"status": TaskStatus.FAILED.value}, windowed=False)
    blocked_n, blocked_chk = count({"status": TaskStatus.BLOCKED.value}, windowed=False)
    waiting_n, waiting_chk = count({"status": TaskStatus.WAITING_FOR_APPROVAL.value}, windowed=False)

    dept_task_ids = [t for t in db.scalars(select(Task.id).where(Task.department_id == dept.id))]
    cost_actual = cost_est = 0
    if dept_task_ids:
        for kind, label in (("actual", "a"), ("estimate", "e")):
            v = int(db.scalar(select(func.coalesce(func.sum(UsageRecord.cost_micro_usd), 0)).where(
                UsageRecord.task_id.in_(dept_task_ids), UsageRecord.cost_kind == kind,
                UsageRecord.ts >= start, UsageRecord.ts < end)) or 0)
            if label == "a":
                cost_actual = v
            else:
                cost_est = v

    completed = db.scalars(select(Task).where(
        Task.department_id == dept.id, Task.status == TaskStatus.COMPLETED.value,
        Task.completed_at >= start, Task.completed_at < end)).all()
    problems = db.scalars(select(Task).where(
        Task.department_id == dept.id, Task.status.in_([TaskStatus.BLOCKED.value, TaskStatus.FAILED.value]))).all()
    dept_agent_ids = [a for a in db.scalars(select(Agent.id).where(Agent.department_id == dept.id))]
    findings = db.scalars(select(BoardEntry).where(
        BoardEntry.responsible_agent_id.in_(dept_agent_ids), BoardEntry.created_at >= start, BoardEntry.created_at < end)
    ).all() if dept_agent_ids else []
    decisions = db.scalars(select(ApprovalRequest).where(
        ApprovalRequest.status == ApprovalStatus.PENDING.value, ApprovalRequest.subject_type == "task",
        ApprovalRequest.subject_id.in_(dept_task_ids or [-1]))).all()

    claims = [
        Claim(f"{who}: {done_n} task(s) completed in the period.", ClaimClass.VERIFIED_FACT, done_n, done_chk, "tasks table", who),
        Claim(f"{created_n} task(s) were created in the period.", ClaimClass.VERIFIED_FACT, created_n, created_chk, "tasks table", who),
        Claim(f"{failed_n} task(s) are in FAILED state.", ClaimClass.VERIFIED_FACT, failed_n, failed_chk, "tasks table", who),
        Claim(f"{blocked_n} task(s) are BLOCKED.", ClaimClass.VERIFIED_FACT, blocked_n, blocked_chk, "tasks table", who),
        Claim(f"{waiting_n} task(s) are waiting for owner approval.", ClaimClass.VERIFIED_FACT, waiting_n, waiting_chk, "tasks table", who),
    ]
    youtube_connected = "youtube" in connected_sources(db)
    if youtube_connected:
        claims.append(Claim("Revenue figures are unavailable: they are not collected (they need a separate monetisation permission and a monetised channel).",
                            ClaimClass.UNAVAILABLE, None, {"type": "source_not_connected", "source": "youtube_revenue"}, "integration status", who))
    else:
        claims.append(Claim("Channel performance metrics (views, watch time, retention, revenue) are unavailable: YouTube is not connected.",
                            ClaimClass.UNAVAILABLE, None, {"type": "source_not_connected", "source": "youtube"}, "integration status", who))
    if dept.slug == "research-growth":
        from app.research import reporting as research_reporting  # local import: it depends on this module

        claims.extend(research_reporting.claims(db, actor, start, end, who))
    if dept.slug in ("analytics-improvement", "community"):
        from app.analytics import reporting as analytics_reporting  # local import: it depends on this module

        claims.extend(analytics_reporting.claims(db, actor, start, end, who, dept.slug))
    claims.extend(extra_claims or [])

    content = {
        "department": dept.slug,
        "reporting_period": window,
        "objectives": [dept.description],
        "completed_work": [{"task_id": t.id, "title": t.title, "completed_at": iso(t.completed_at)} for t in completed],
        "evidence_of_completion": [
            {"task_id": t.id, "evidence": [{"id": e.id, "kind": e.kind, "status": e.verification_status}
                                           for e in evidence.for_task(db, t.id)]} for t in completed
        ],
        "performance_metrics": ({"status": "see_analytics_report", "reason": "Performance figures are collected from connected channels; the Analytics and Improvement report lists what arrived and what is unavailable."}
                                if youtube_connected else {"status": "unavailable", "reason": "YouTube analytics are not connected yet."}),
        "problems_and_blockers": [
            {"task_id": t.id, "title": t.title, "status": t.status, "reason": t.blocked_reason or (t.error or {}).get("message")}
            for t in problems
        ],
        "cost_and_resource_usage": {
            "actual_usd": cost_actual / finance.MICRO, "estimated_usd": cost_est / finance.MICRO,
            "note": "Sum of usage records linked to this department's tasks.",
        },
        "key_findings": [{"board_id": e.public_id, "title": e.title, "status": e.status} for e in findings],
        "recommendations": [],
        "confidence_and_uncertainty": "Counts come directly from task records; metrics that depend on external providers are unavailable.",
        "required_decisions": [{"approval_id": r.id, "title": r.title} for r in decisions],
        "related_board_entries": [e.public_id for e in findings],
        "claims": [c.as_dict() for c in claims],
        "method": METHOD,
    }
    report = Report(
        kind=ReportKind.DEPARTMENT.value, title=f"{dept.name} report", period_start=start, period_end=end,
        department_id=dept.id, author=actor.label, status="SUBMITTED", content=content,
        board_version=board.current_release_version(db),
        summary_text=f"{done_n} completed, {blocked_n} blocked, {failed_n} failed.",
    )
    db.add(report)
    db.flush()
    audit.record(db, actor, "report.department_submitted", "report", report.id, {"department": dept.slug})
    return report


# --- COO consolidation ----------------------------------------------------------------------------------------


def consolidate(db: Session, actor: Actor, start: datetime, end: datetime, report_ids: list[int] | None = None) -> Report:
    actor.require(P.REPORT_CONSOLIDATE)
    depts = list(db.scalars(select(Department).order_by(Department.sort_order)))
    if report_ids:
        reports = [db.get(Report, i) for i in report_ids]
    else:  # newest department report per department for this exact period
        reports = []
        for d in depts:
            r = db.scalar(select(Report).where(
                Report.kind == ReportKind.DEPARTMENT.value, Report.department_id == d.id,
                Report.period_start == start, Report.period_end == end).order_by(Report.id.desc()))
            if r is not None:
                reports.append(r)
    reports = [r for r in reports if r is not None]
    if any(r.kind != ReportKind.DEPARTMENT.value for r in reports):
        raise ReportError("only department reports can be consolidated")
    have = {r.department_id for r in reports}
    missing = [d.slug for d in depts if d.id not in have]

    claims: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    disagreements: list[dict[str, Any]] = []
    for r in reports:
        for c in r.content.get("claims", []):
            c = {**c, "department": r.content["department"]}
            sig = _sig(c.get("check"))
            if sig and sig in seen and seen[sig].get("value") != c.get("value"):
                disagreements.append({
                    "topic": c["text"], "values": [seen[sig].get("value"), c.get("value")],
                    "departments": [seen[sig]["department"], c["department"]],
                })
            elif sig:
                seen[sig] = c
            claims.append(c)

    blockers = [dict(p, department=r.content["department"]) for r in reports for p in r.content["problems_and_blockers"]]
    content = {
        "reporting_period": {"start": start.isoformat(), "end": end.isoformat()},
        "departments_reported": [r.content["department"] for r in reports],
        "departments_missing": missing,
        "department_summaries": {r.content["department"]: r.summary_text for r in reports},
        "claims": claims,
        "disagreements": disagreements,
        "cross_department_blockers": blockers,
        "required_decisions": [d for r in reports for d in r.content["required_decisions"]],
        "method": METHOD,
        "note": ("Departments without a report are listed as missing; they are not assumed to have "
                 "nothing to report." if missing else "All departments reported."),
    }
    rep = Report(
        kind=ReportKind.OPERATIONS.value, title="Operations summary", period_start=start, period_end=end,
        author=actor.label, status="SUBMITTED", content=content, parent_report_ids=[r.id for r in reports],
        board_version=board.current_release_version(db),
        summary_text=f"{len(reports)} of {len(depts)} departments reported; {len(blockers)} open blocker(s).",
    )
    db.add(rep)
    db.flush()
    audit.record(db, actor, "report.operations_consolidated", "report", rep.id,
                 {"departments": len(reports), "missing": missing})
    return rep


# --- CEO verification -------------------------------------------------------------------------------------------


def _ceo_snapshot(db: Session) -> dict[str, Any]:
    """Facts the CEO reads straight from the records, independent of any manager's report."""
    tasks = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
    videos = dict(db.execute(select(Video.stage, func.count()).group_by(Video.stage)).all())
    pending = db.scalar(select(func.count()).select_from(ApprovalRequest).where(
        ApprovalRequest.status == ApprovalStatus.PENDING.value)) or 0
    boards = dict(db.execute(select(BoardEntry.status, func.count()).group_by(BoardEntry.status)).all())
    return {"tasks_by_status": tasks, "videos_by_stage": videos, "pending_approvals": pending, "board_by_status": boards}


def _compact_sections(sections: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Keep the owner-facing report concise without discarding anything the CEO verified.

    * Verified zero counts ("0 tasks failed") are folded into a per-department "quiet" note; the
      individual checks remain in the CEO verification detail (diagnostics).
    * The same unavailable-data statement from several departments is shown once.
    """
    material: list[dict[str, Any]] = []
    zero_depts: dict[str, int] = {}
    active_depts: set[str] = set()
    for f in sections["verified_facts"]:
        dept = f.get("department")
        if f.get("value") == 0 and dept:
            zero_depts[dept] = zero_depts.get(dept, 0) + 1
        else:
            material.append(f)
            if dept:
                active_depts.add(dept)
    sections["verified_facts"] = material

    merged: dict[str, dict[str, Any]] = {}
    for u in sections["unavailable_data"]:
        entry = merged.setdefault(u["statement"], {"statement": u["statement"], "evidence_note": u.get("evidence_note"),
                                                   "reported_by": []})
        entry["reported_by"].append(u.get("department"))
    sections["unavailable_data"] = [
        {**e, "reported_by": sorted({d for d in e["reported_by"] if d})} for e in merged.values()
    ]
    return [
        {"department": d, "verified_zero_checks": n,
         "statement": "No task activity or open problems, verified from task records."}
        for d, n in zero_depts.items() if d not in active_depts
    ]


def verify_and_publish(db: Session, actor: Actor, operations_report_id: int) -> Report:
    """The CEO re-checks every claim and issues the executive report."""
    actor.require(P.REPORT_VERIFY)
    ops = db.get(Report, operations_report_id)
    if ops is None or ops.kind != ReportKind.OPERATIONS.value:
        raise ReportError("an operations report is required")
    start, end = ops.period_start, ops.period_end

    sections: dict[str, list[dict[str, Any]]] = {
        "verified_facts": [], "unverified_claims": [], "rejected_claims": [], "estimates": [], "forecasts": [],
        "hypotheses": [], "recommendations": [], "implemented_decisions": [], "needs_owner_approval": [],
        "unavailable_data": [],
    }
    results: list[dict[str, Any]] = []
    summary = {"total": 0, "confirmed": 0, "contradicted": 0, "unverifiable": 0}
    disagreements = list(ops.content.get("disagreements", []))

    for c in ops.content.get("claims", []):
        cls = ClaimClass(c["cls"])
        res = run_check(db, c.get("check"), actor)
        summary["total"] += 1
        summary[res.status] += 1
        results.append({"text": c["text"], "reporter": c.get("reporter"), "department": c.get("department"),
                        "class": cls.value, "check": c.get("check"), "result": res.status,
                        "actual": res.actual, "detail": res.detail})
        entry = {"text": c["text"], "department": c.get("department"), "source": c.get("source"), "value": c.get("value")}
        if res.status == "contradicted":
            sections["rejected_claims"].append({**entry, "reported_value": c.get("value"), "database_value": res.actual,
                                                 "detail": res.detail})
            disagreements.append({"topic": c["text"], "reported": c.get("value"), "verified": res.actual,
                                  "most_reliable_source": "direct record check by the CEO"})
        elif cls in (ClaimClass.VERIFIED_FACT, ClaimClass.UNVERIFIED_CLAIM, ClaimClass.IMPLEMENTED_DECISION):
            bucket = {
                ("confirmed", ClaimClass.VERIFIED_FACT): "verified_facts",
                ("confirmed", ClaimClass.IMPLEMENTED_DECISION): "implemented_decisions",
            }.get((res.status, cls), "unverified_claims")
            sections[bucket].append({**entry, "evidence_note": res.detail})
        elif cls is ClaimClass.UNAVAILABLE:
            if res.status == "confirmed":
                sections["unavailable_data"].append({"statement": c["text"], "evidence_note": res.detail,
                                                   "department": c.get("department")})
            else:
                sections["unverified_claims"].append({**entry, "evidence_note": res.detail})
        else:
            key = {
                ClaimClass.ESTIMATE: "estimates", ClaimClass.FORECAST: "forecasts", ClaimClass.HYPOTHESIS: "hypotheses",
                ClaimClass.RECOMMENDATION: "recommendations", ClaimClass.NEEDS_OWNER_APPROVAL: "needs_owner_approval",
            }[cls]
            sections[key].append({**entry, "note": "Not independently verifiable; shown with its stated class."})

    quiet_departments = _compact_sections(sections)

    snap = _ceo_snapshot(db)
    chain = audit.verify_chain(db)
    mode = settings_svc.operating_mode(db)
    paused = settings_svc.is_emergency_paused(db)
    budget = settings_svc.get(db, "budget")
    risks: list[dict[str, Any]] = []
    blocked = snap["tasks_by_status"].get(TaskStatus.BLOCKED.value, 0)
    failed = snap["tasks_by_status"].get(TaskStatus.FAILED.value, 0)
    if not chain["ok"]:
        risks.append({"severity": "critical", "text": f"Audit log integrity check FAILED at event {chain['first_bad_id']}."})
    if paused:
        risks.append({"severity": "high", "text": "Emergency pause is active; no new work is starting."})
    if blocked:
        risks.append({"severity": "medium", "text": f"{blocked} task(s) are blocked."})
    if failed:
        risks.append({"severity": "medium", "text": f"{failed} task(s) have failed."})
    if snap["pending_approvals"]:
        risks.append({"severity": "low", "text": f"{snap['pending_approvals']} approval request(s) are waiting for you."})
    if budget["daily_usd"] == 0 and budget["monthly_usd"] == 0:
        risks.append({"severity": "info", "text": "Spending limits are $0: no paid operations are permitted until you set limits."})
    if ops.content.get("departments_missing"):
        risks.append({"severity": "medium", "text": "No report received from: " + ", ".join(ops.content["departments_missing"]) + "."})
    if sections["rejected_claims"]:
        risks.append({"severity": "high", "text": f"{len(sections['rejected_claims'])} reported claim(s) were contradicted by the records and rejected."})

    priorities = []
    if snap["pending_approvals"]:
        priorities.append("Decide the pending approval requests in the Approval Center.")
    if blocked:
        priorities.append("Resolve blocked tasks (see Organization).")
    if failed:
        priorities.append("Review failed tasks and decide whether to retry.")
    if not priorities:
        priorities.append("No urgent items. Continue the planned production schedule.")

    n_created = db.scalar(select(func.count()).select_from(Task).where(Task.created_at >= start, Task.created_at < end)) or 0
    n_done = db.scalar(select(func.count()).select_from(Task).where(Task.completed_at >= start, Task.completed_at < end)) or 0
    if n_created == 0 and n_done == 0:
        headline = "No tasks were created or completed in this period."
    else:
        headline = f"{n_done} task(s) completed and {n_created} created in this period; {len(risks)} item(s) flagged."
    if summary["contradicted"]:
        headline += f" {summary['contradicted']} reported claim(s) were rejected after verification."

    content = {
        "generated_at": iso(utcnow()), "method": METHOD, "period": {"start": iso(start), "end": iso(end)},
        "headline": headline,
        "company_status": {"operating_mode": mode.value, "emergency_pause": paused, "audit_chain_ok": chain["ok"],
                           "audit_events_checked": chain["checked"]},
        "company_snapshot": snap,
        "sections": sections,
        "quiet_departments": quiet_departments,
        "disagreements": disagreements,
        "risks": risks,
        "next_priorities": priorities,
        "check_summary": summary,
        "department_coverage": {"reported": ops.content.get("departments_reported", []),
                                "missing": ops.content.get("departments_missing", [])},
        "metrics_availability": {
            "youtube_analytics": {"available": False, "reason": "No YouTube channel is connected."},
            "revenue": {"available": False, "reason": "No authorised revenue source is connected."},
        },
        "legend": {"verified_facts": "Re-checked by the CEO against records.",
                   "unverified_claims": "Stated by a manager; the CEO could not verify them.",
                   "rejected_claims": "Contradicted by the records; not used."},
    }
    ex = Report(
        kind=ReportKind.EXECUTIVE.value, title="Executive report", period_start=start, period_end=end,
        author=actor.label, status="VERIFIED", content=content, parent_report_ids=[ops.id],
        board_version=board.current_release_version(db),
        verification={"results": results, "summary": summary, "verifier": actor.label,
                      "verified_at": iso(utcnow())},
        summary_text=headline,
    )
    db.add(ex)
    ops.status = "VERIFIED"
    db.flush()
    audit.record(db, actor, "report.executive_verified", "report", ex.id, {"summary": summary})
    return ex


def run_reporting_cycle(db: Session, start: datetime | None = None, end: datetime | None = None) -> Report:
    """Build all department reports, consolidate, verify. Used by the scheduler and the dashboard."""
    end = end or utcnow()
    start = start or end - timedelta(days=1)
    system = Actor.system("reporting")
    coo = org.actor_for_slug(db, "coo")
    ceo = org.actor_for_slug(db, "ceo")
    for d in db.scalars(select(Department)):
        build_department_report(db, system, d.slug, start, end)
    ops = consolidate(db, coo, start, end)
    return verify_and_publish(db, ceo, ops.id)


# --- reading --------------------------------------------------------------------------------------------------------


def latest_executive(db: Session) -> Report | None:
    return db.scalar(select(Report).where(Report.kind == ReportKind.EXECUTIVE.value).order_by(Report.id.desc()))


def serialize(r: Report, *, include_verification: bool = False) -> dict[str, Any]:
    out = {
        "id": r.id, "kind": r.kind, "title": r.title, "period_start": iso(r.period_start),
        "period_end": iso(r.period_end), "author": r.author, "status": r.status, "created_at": iso(r.created_at),
        "summary": r.summary_text, "content": r.content, "board_version": r.board_version,
        "parent_report_ids": r.parent_report_ids,
    }
    if include_verification:
        out["verification"] = r.verification
    return out
