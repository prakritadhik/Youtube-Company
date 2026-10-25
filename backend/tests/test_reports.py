from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.enums import ClaimClass, ReportKind
from app.models.audit import AuditEvent
from app.models.reports import Report
from app.security.permissions import PermissionDenied
from app.services import reports
from app.services.reports import Claim
from app.timeutil import utcnow
from tests.helpers import verified_task

_cached: dict = {}


@pytest.fixture(autouse=True)
def _fresh_window():
    _cached.clear()
    yield


def window():
    """One stable reporting period per test (a scheduled cycle uses one period for all reports)."""
    if "w" not in _cached:
        end = utcnow() + timedelta(minutes=1)
        _cached["w"] = (end - timedelta(days=1), end)
    return _cached["w"]


def prod_report(db, agent, system, extra=None):
    s, e = window()
    return reports.build_department_report(db, agent("production-manager"), "production", s, e, extra)


def test_department_report_has_all_required_sections(db, agent):
    verified_task(db, agent, "script-lyrics-writer", "production-manager", "Write script")
    r = prod_report(db, agent, None)
    c = r.content
    for key in ("reporting_period", "objectives", "completed_work", "evidence_of_completion", "performance_metrics",
                "problems_and_blockers", "cost_and_resource_usage", "key_findings", "recommendations",
                "confidence_and_uncertainty", "required_decisions", "related_board_entries", "claims"):
        assert key in c, key
    assert len(c["completed_work"]) == 1 and c["evidence_of_completion"][0]["evidence"]
    assert c["performance_metrics"]["status"] == "unavailable"


def test_only_a_departments_own_manager_may_submit_its_report(db, agent):
    s, e = window()
    with pytest.raises(PermissionDenied):
        reports.build_department_report(db, agent("research-manager"), "production", s, e)
    with pytest.raises(PermissionDenied):
        reports.build_department_report(db, agent("script-lyrics-writer"), "production", s, e)


def test_full_cycle_produces_a_verified_executive_report(db, agent):
    verified_task(db, agent, "script-lyrics-writer", "production-manager", "Write script")
    verified_task(db, agent, "trend-research-specialist", "research-manager", "Find trends")
    ex = reports.run_reporting_cycle(db, *window())
    assert ex.kind == ReportKind.EXECUTIVE.value and ex.status == "VERIFIED"
    c = ex.content
    assert c["check_summary"]["contradicted"] == 0 and c["check_summary"]["confirmed"] > 0
    assert any("completed" in f["text"] for f in c["sections"]["verified_facts"])
    assert c["department_coverage"]["missing"] == []
    assert "no language model" in c["method"]
    ops = db_report(db, ex.parent_report_ids[0])
    assert ops.kind == ReportKind.OPERATIONS.value and len(ops.parent_report_ids) == 7


def db_report(db, rid):
    return db.get(Report, rid)


def test_ceo_rejects_a_fabricated_manager_claim(db, agent):
    verified_task(db, agent, "script-lyrics-writer", "production-manager", "Write script")
    s, e = window()
    lie = Claim(
        "Production completed 50 tasks this period.", ClaimClass.VERIFIED_FACT, 50,
        {"type": "count", "entity": "tasks", "where": {"status": "COMPLETED"}, "expected": 50,
         "between": {"column": "completed_at", "start": s.isoformat(), "end": e.isoformat()}},
        "production manager", "Production Manager")
    reports.build_department_report(db, agent("production-manager"), "production", s, e, [lie])
    ops = reports.consolidate(db, agent("coo"), s, e)
    ex = reports.verify_and_publish(db, agent("ceo"), ops.id)
    rejected = ex.content["sections"]["rejected_claims"]
    assert len(rejected) == 1 and rejected[0]["reported_value"] == 50 and rejected[0]["database_value"] == 1
    assert not any("50 tasks" in f["text"] for f in ex.content["sections"]["verified_facts"])
    assert any(d.get("verified") == 1 for d in ex.content["disagreements"])
    assert any(r["severity"] == "high" and "contradicted" in r["text"] for r in ex.content["risks"])
    assert "rejected after verification" in ex.content["headline"]
    assert any(r["result"] == "contradicted" for r in ex.verification["results"])  # Layer B detail kept


def test_claims_without_evidence_are_never_presented_as_facts(db, agent):
    s, e = window()
    vague = Claim("Audience loves the new acoustic style.", ClaimClass.VERIFIED_FACT, None, None, "gut feeling", "Production Manager")
    reports.build_department_report(db, agent("production-manager"), "production", s, e, [vague])
    ex = reports.verify_and_publish(db, agent("ceo"), reports.consolidate(db, agent("coo"), s, e).id)
    texts_facts = [f["text"] for f in ex.content["sections"]["verified_facts"]]
    assert "Audience loves the new acoustic style." not in texts_facts
    assert any(u["text"] == "Audience loves the new acoustic style." for u in ex.content["sections"]["unverified_claims"])


def test_estimates_forecasts_hypotheses_and_recommendations_stay_labelled(db, agent):
    s, e = window()
    extra = [Claim("Next month will reach 1k views.", ClaimClass.FORECAST, None, None, "model", "Mgr"),
             Claim("Cost per video is about $0.40.", ClaimClass.ESTIMATE, None, None, "model", "Mgr"),
             Claim("Shorter intros may help.", ClaimClass.HYPOTHESIS, None, None, "model", "Mgr"),
             Claim("Try a dance-themed song.", ClaimClass.RECOMMENDATION, None, None, "model", "Mgr")]
    reports.build_department_report(db, agent("production-manager"), "production", s, e, extra)
    sec = reports.verify_and_publish(db, agent("ceo"), reports.consolidate(db, agent("coo"), s, e).id).content["sections"]
    assert sec["forecasts"] and sec["estimates"] and sec["hypotheses"] and sec["recommendations"]
    assert all("1k views" not in f["text"] for f in sec["verified_facts"])


def test_missing_department_reports_are_listed_not_assumed(db, agent):
    s, e = window()
    prod_report(db, agent, None)
    ops = reports.consolidate(db, agent("coo"), s, e)
    assert "production" not in ops.content["departments_missing"] and len(ops.content["departments_missing"]) == 6
    ex = reports.verify_and_publish(db, agent("ceo"), ops.id)
    assert len(ex.content["department_coverage"]["missing"]) == 6
    assert any("No report received" in r["text"] for r in ex.content["risks"])


def test_unavailable_metrics_are_not_turned_into_zero(db, agent):
    ex = reports.run_reporting_cycle(db, *window())
    assert ex.content["metrics_availability"]["youtube_analytics"]["available"] is False
    assert ex.content["sections"]["unavailable_data"]
    blob = str(ex.content)
    assert "'views'" not in blob and "views: 0" not in blob


def test_false_unavailability_claim_is_caught_when_source_is_connected(db, agent, monkeypatch):
    s, e = window()
    prod_report(db, agent, None)
    ops = reports.consolidate(db, agent("coo"), s, e)
    monkeypatch.setattr(reports, "connected_sources", lambda _db: {"youtube"})
    ex = reports.verify_and_publish(db, agent("ceo"), ops.id)
    assert ex.content["check_summary"]["contradicted"] >= 1  # "YouTube is not connected" is now false


def test_quiet_period_is_reported_honestly_not_invented(db, agent):
    ex = reports.run_reporting_cycle(db, *window())
    assert ex.content["headline"].startswith("No tasks were created or completed")
    # verified zeros are reported as "quiet departments", not silently dropped and not listed 35 times
    assert len(ex.content["quiet_departments"]) == 7 and ex.content["sections"]["verified_facts"] == []
    assert all(q["verified_zero_checks"] == 5 and "verified from task records" in q["statement"]
               for q in ex.content["quiet_departments"])
    assert ex.content["check_summary"]["confirmed"] == 42  # every check was still run


def test_a_manager_report_that_went_stale_before_verification_is_flagged(db, agent):
    """The CEO compares against the records *now*, so a number that has since changed is not trusted."""
    verified_task(db, agent, "script-lyrics-writer", "production-manager", "Before")
    s, e = window()
    r = reports.build_department_report(db, agent("production-manager"), "production", s, e)
    ops = reports.consolidate(db, agent("coo"), s, e, [r.id])
    verified_task(db, agent, "script-lyrics-writer", "production-manager", "After")  # inside the same window
    ex = reports.verify_and_publish(db, agent("ceo"), ops.id)
    assert ex.content["check_summary"]["contradicted"] >= 1
    assert any("completed" in c["text"] for c in ex.content["sections"]["rejected_claims"])


def test_evidence_claims_are_rechecked_by_the_ceo(db, agent):
    t = verified_task(db, agent, "script-lyrics-writer", "production-manager", "Script")
    from app.models.tasks import Evidence

    ev = db.query(Evidence).filter_by(task_id=t.id).one()
    s, e = window()
    good = Claim("The script file exists.", ClaimClass.VERIFIED_FACT, None, {"type": "evidence", "id": ev.id}, "file", "PM")
    reports.build_department_report(db, agent("production-manager"), "production", s, e, [good])
    ex = reports.verify_and_publish(db, agent("ceo"), reports.consolidate(db, agent("coo"), s, e).id)
    assert any(f["text"] == "The script file exists." for f in ex.content["sections"]["verified_facts"])

    from app.storage import resolve_data_path

    resolve_data_path(ev.ref).write_bytes(b"changed")
    reports.build_department_report(db, agent("production-manager"), "production", s, e, [good])
    ex2 = reports.verify_and_publish(db, agent("ceo"), reports.consolidate(db, agent("coo"), s, e).id)
    assert any(r["text"] == "The script file exists." for r in ex2.content["sections"]["rejected_claims"])


def test_check_descriptors_cannot_probe_arbitrary_columns(db, agent, ceo):
    r = reports.run_check(db, {"type": "count", "entity": "users", "where": {}, "expected": 0}, ceo)
    assert r.status == "unverifiable"
    r = reports.run_check(db, {"type": "count", "entity": "tasks", "where": {"result": "x"}, "expected": 0}, ceo)
    assert r.status == "unverifiable" and "cannot be used" in r.detail


def test_audit_tampering_surfaces_as_a_critical_risk(db, agent):
    db.commit()
    db.execute(text("UPDATE audit_events SET action = 'forged' WHERE id = (SELECT MIN(id) FROM audit_events)"))
    db.commit()
    ex = reports.run_reporting_cycle(db, *window())
    assert any(r["severity"] == "critical" for r in ex.content["risks"])
    assert ex.content["company_status"]["audit_chain_ok"] is False
    assert db.query(AuditEvent).count() > 0


def test_permissions_on_consolidation_and_verification(db, agent):
    s, e = window()
    prod_report(db, agent, None)
    with pytest.raises(PermissionDenied):
        reports.consolidate(db, agent("production-manager"), s, e)
    ops = reports.consolidate(db, agent("coo"), s, e)
    with pytest.raises(PermissionDenied):
        reports.verify_and_publish(db, agent("coo"), ops.id)
    dept = db.query(Report).filter_by(kind="department").first()
    with pytest.raises(reports.ReportError):
        reports.verify_and_publish(db, agent("ceo"), dept.id)


def test_disagreeing_departments_are_flagged(db, agent):
    s, e = window()
    chk = {"type": "count", "entity": "videos", "where": {}, "expected": 3}
    a = Claim("We published 3 videos.", ClaimClass.VERIFIED_FACT, 3, chk, "x", "PM")
    b = Claim("We published 5 videos.", ClaimClass.VERIFIED_FACT, 5, {**chk, "expected": 5}, "y", "Pub")
    reports.build_department_report(db, agent("production-manager"), "production", s, e, [a])
    reports.build_department_report(db, agent("publishing-manager"), "publishing", s, e, [b])
    ops = reports.consolidate(db, agent("coo"), s, e)
    assert ops.content["disagreements"] and set(ops.content["disagreements"][0]["departments"]) == {"production", "publishing"}


def test_owner_report_stays_concise_but_nothing_is_dropped_from_the_ceo_record(db, agent):
    verified_task(db, agent, "script-lyrics-writer", "production-manager", "Write script")
    ex = reports.run_reporting_cycle(db, *window())
    facts = ex.content["sections"]["verified_facts"]
    assert 0 < len(facts) <= 3 and all(f["value"] for f in facts)          # only material facts in Layer C
    assert "production" not in {q["department"] for q in ex.content["quiet_departments"]}
    assert len(ex.verification["results"]) == 42                              # all checks kept in Layer B
    unavailable = ex.content["sections"]["unavailable_data"]
    assert len(unavailable) == 1 and len(unavailable[0]["reported_by"]) == 7  # said once, with who reported it
