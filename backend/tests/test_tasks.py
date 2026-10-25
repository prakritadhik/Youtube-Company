from __future__ import annotations

from datetime import timedelta

import pytest

from app.enums import ApprovalKind
from app.enums import TaskStatus as S
from app.models.tasks import Evidence, Task
from app.security.permissions import PermissionDenied
from app.services import approvals, control, evidence, finance, tasks
from app.services import settings as settings_svc
from app.storage import resolve_data_path
from app.timeutil import utcnow
from tests.helpers import file_evidence


def _run_to_submitted(db, system, worker, task, evidence_specs):
    tasks.claim_next(db, system, "w", {task.task_type})
    db.refresh(task)
    return tasks.finish_run(db, system, task.id, "w", result={"ok": True}, evidence_specs=evidence_specs)


def test_assignment_follows_the_reporting_line(db, agent, owner, system):
    pm, rm, coo = agent("production-manager"), agent("research-manager"), agent("coo")
    t = tasks.create(db, pm, title="Write script", assigned_agent_slug="script-lyrics-writer")
    assert t.status == S.QUEUED.value and t.department_id is not None
    with pytest.raises(PermissionDenied):  # a manager cannot assign across departments
        tasks.create(db, rm, title="x", assigned_agent_slug="script-lyrics-writer")
    with pytest.raises(PermissionDenied):  # a worker cannot assign to a peer
        tasks.create(db, agent("script-lyrics-writer"), title="x", assigned_agent_slug="research-specialist")
    tasks.create(db, coo, title="ok", assigned_agent_slug="script-lyrics-writer")
    tasks.create(db, owner, title="ok", assigned_agent_slug="script-lyrics-writer")
    tasks.create(db, system, title="ok", assigned_agent_slug="script-lyrics-writer")


def test_worker_cannot_complete_its_own_task_manager_must_verify(db, agent, system):
    pm = agent("production-manager")
    t = tasks.create(db, pm, title="Write script", task_type="manual", assigned_agent_slug="script-lyrics-writer")
    writer = agent("script-lyrics-writer")
    tasks.begin(db, writer, t.id)
    tasks.submit(db, writer, t.id, result={"words": 100}, evidence_specs=[file_evidence("script.txt")])
    db.refresh(t)
    assert t.status == S.SUBMITTED.value
    with pytest.raises(PermissionDenied):
        tasks.review(db, writer, t.id, approve=True)  # no review permission
    with pytest.raises(PermissionDenied):
        tasks.review(db, agent("research-manager"), t.id, approve=True)  # wrong department's manager
    with pytest.raises(PermissionDenied):
        tasks.review(db, agent("script-lyrics-writer"), t.id, approve=True)
    tasks.review(db, pm, t.id, approve=True, notes="read the file")
    db.refresh(t)
    assert t.status == S.COMPLETED.value and t.completed_at is not None


def test_coo_and_ceo_may_review_in_the_chain_of_command(db, agent):
    pm = agent("production-manager")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    w = agent("script-lyrics-writer")
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={}, evidence_specs=[file_evidence("a.txt")])
    tasks.review(db, agent("coo"), t.id, approve=True)
    assert db.get(Task, t.id).status == S.COMPLETED.value


def test_note_only_or_missing_evidence_cannot_complete_a_task(db, agent):
    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={"claim": "done!"}, evidence_specs=[{"kind": "note", "ref": "trust me"}])
    with pytest.raises(tasks.TaskError, match="no machine-verifiable evidence"):
        tasks.review(db, pm, t.id, approve=True)
    assert db.get(Task, t.id).status == S.SUBMITTED.value


def test_no_evidence_at_all_cannot_complete(db, agent):
    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={})
    with pytest.raises(tasks.TaskError):
        tasks.review(db, pm, t.id, approve=True)


def test_evidence_changed_after_submission_fails_review(db, agent):
    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    spec = file_evidence("doc.txt", b"original")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={}, evidence_specs=[spec])
    resolve_data_path(spec["ref"]).write_bytes(b"tampered after the fact")
    with pytest.raises(tasks.TaskError, match="failed verification"):
        tasks.review(db, pm, t.id, approve=True)
    ev = db.query(Evidence).filter_by(task_id=t.id).one()
    assert ev.verification_status == "failed" and "hash mismatch" in ev.verification_detail


def test_deleted_evidence_file_fails_review(db, agent):
    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    spec = file_evidence("gone.txt")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={}, evidence_specs=[spec])
    resolve_data_path(spec["ref"]).unlink()
    with pytest.raises(tasks.TaskError, match="failed verification"):
        tasks.review(db, pm, t.id, approve=True)


def test_evidence_cannot_escape_the_data_directory(db, agent):
    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, w, t.id)
    for bad in ("../../etc/passwd", "/etc/passwd", "C:\\Windows\\win.ini", "a/../../x"):
        with pytest.raises(evidence.EvidenceError):
            evidence.add(db, w, task_id=t.id, kind=evidence.EvidenceKind.FILE, ref=bad)


def test_simulated_api_response_is_not_accepted_as_proof(db, agent):
    from app.storage import write_json_artifact

    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    rel = write_json_artifact("tests", "resp.json", {"provider": "elevenlabs", "status_code": 200, "simulated": True})
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={}, evidence_specs=[{"kind": "api_response", "ref": rel}])
    with pytest.raises(tasks.TaskError, match="failed verification"):
        tasks.review(db, pm, t.id, approve=True)


def test_error_api_response_is_not_accepted_as_proof(db, agent):
    from app.storage import write_json_artifact

    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    rel = write_json_artifact("tests", "err.json", {"provider": "youtube", "status_code": 403})
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={}, evidence_specs=[{"kind": "api_response", "ref": rel}])
    with pytest.raises(tasks.TaskError):
        tasks.review(db, pm, t.id, approve=True)


def test_revision_cycle(db, agent):
    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={}, evidence_specs=[file_evidence("v1.txt")])
    with pytest.raises(tasks.TaskError):
        tasks.review(db, pm, t.id, approve=False)  # notes required
    tasks.review(db, pm, t.id, approve=False, notes="chorus too similar to the last song")
    db.refresh(t)
    assert t.status == S.REVISION_REQUESTED.value and t.revision_count == 1
    tasks.begin(db, w, t.id)
    tasks.submit(db, w, t.id, result={}, evidence_specs=[file_evidence("v2.txt")])
    tasks.review(db, pm, t.id, approve=True)
    assert db.get(Task, t.id).status == S.COMPLETED.value


def test_review_requires_submitted_status(db, agent):
    pm = agent("production-manager")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    with pytest.raises(tasks.InvalidTransition):
        tasks.review(db, pm, t.id, approve=True)


def test_agent_tasks_cannot_skip_review_but_system_tasks_can(db, agent, system):
    with pytest.raises(tasks.TaskError):
        tasks.create(db, agent("production-manager"), title="s", assigned_agent_slug="script-lyrics-writer",
                     review_required=False)
    t = tasks.create(db, system, title="maintenance", task_type="system.noop", review_required=False)
    tasks.claim_next(db, system, "w", {"system.noop"})
    out = tasks.finish_run(db, system, t.id, "w", result={"ok": 1})
    assert out.status == S.COMPLETED.value


# --- dependencies ---------------------------------------------------------------------------------


def test_dependencies_gate_execution_and_promote_on_completion(db, system):
    a = tasks.create(db, system, title="A", task_type="t", review_required=False)
    b = tasks.create(db, system, title="B", task_type="t", review_required=False, depends_on=[a.id])
    assert b.status == S.WAITING_FOR_DEPENDENCY.value
    claimed = tasks.claim_next(db, system, "w", {"t"})
    assert claimed.id == a.id
    assert tasks.claim_next(db, system, "w2", {"t"}) is None  # B is not runnable
    tasks.finish_run(db, system, a.id, "w", result={})
    db.refresh(b)
    assert b.status == S.QUEUED.value
    assert tasks.claim_next(db, system, "w", {"t"}).id == b.id


def test_dependency_cycles_are_rejected(db, system):
    a = tasks.create(db, system, title="A", task_type="t", review_required=False)
    b = tasks.create(db, system, title="B", task_type="t", review_required=False, depends_on=[a.id])
    with pytest.raises(tasks.TaskError, match="cycle"):
        tasks.add_dependency(db, system, a.id, b.id)
    with pytest.raises(tasks.TaskError):
        tasks.add_dependency(db, system, a.id, a.id)


def test_failed_dependency_blocks_dependents(db, system):
    a = tasks.create(db, system, title="A", task_type="t", review_required=False, max_attempts=1)
    b = tasks.create(db, system, title="B", task_type="t", review_required=False, depends_on=[a.id])
    tasks.claim_next(db, system, "w", {"t"})
    tasks.fail_run(db, system, a.id, "w", "boom")
    db.refresh(a), db.refresh(b)
    assert a.status == S.FAILED.value and b.status == S.BLOCKED.value and "dependency" in b.blocked_reason


def test_cancel_cascades_to_dependents(db, system, owner):
    a = tasks.create(db, system, title="A", task_type="t", review_required=False)
    b = tasks.create(db, system, title="B", task_type="t", review_required=False, depends_on=[a.id])
    tasks.cancel(db, owner, a.id, "not needed")
    db.refresh(b)
    assert b.status == S.BLOCKED.value


# --- retries, leases, recovery ------------------------------------------------------------------------


def test_bounded_exponential_backoff_then_failure(db, system):
    t = tasks.create(db, system, title="flaky", task_type="t", review_required=False)  # max_attempts=3
    base = utcnow()
    delays = []
    for attempt in range(1, 4):
        claimed = tasks.claim_next(db, system, "w", {"t"}, now=base + timedelta(hours=attempt))
        assert claimed is not None and claimed.attempts == attempt
        tasks.fail_run(db, system, t.id, "w", "provider 503", now=base)
        db.refresh(t)
        if t.status == S.RETRY_SCHEDULED.value:
            delays.append(int((t.next_run_at - base).total_seconds()))
    assert delays == [10, 20]  # base 10s doubled
    assert t.status == S.FAILED.value and t.attempts == 3
    assert tasks.claim_next(db, system, "w", {"t"}, now=base + timedelta(days=1)) is None  # no infinite loop


def test_retry_is_not_claimable_before_its_time(db, system):
    t = tasks.create(db, system, title="x", task_type="t", review_required=False)
    now = utcnow()
    tasks.claim_next(db, system, "w", {"t"}, now=now)
    tasks.fail_run(db, system, t.id, "w", "oops", now=now)
    assert tasks.claim_next(db, system, "w", {"t"}, now=now + timedelta(seconds=5)) is None
    assert tasks.claim_next(db, system, "w", {"t"}, now=now + timedelta(seconds=11)).id == t.id


def test_non_retryable_failure_fails_immediately(db, system):
    t = tasks.create(db, system, title="x", task_type="t", review_required=False)
    tasks.claim_next(db, system, "w", {"t"})
    tasks.fail_run(db, system, t.id, "w", "bad input", retryable=False)
    assert db.get(Task, t.id).status == S.FAILED.value


def test_error_details_are_redacted(db, system):
    t = tasks.create(db, system, title="x", task_type="t", review_required=False, max_attempts=1)
    tasks.claim_next(db, system, "w", {"t"})
    tasks.fail_run(db, system, t.id, "w", "401 for key sk-ant-api03-abcdefghijklmnop")
    assert "sk-ant" not in db.get(Task, t.id).error["message"]


def test_stuck_task_recovers_after_lease_expiry(db, system):
    t = tasks.create(db, system, title="x", task_type="t", review_required=False)
    tasks.claim_next(db, system, "w", {"t"})
    assert tasks.recover_stuck(db, system) == []  # lease still valid
    t = db.get(Task, t.id)
    t.lease_expires_at = utcnow() - timedelta(seconds=1)
    db.flush()
    assert tasks.recover_stuck(db, system) == [t.id]
    db.refresh(t)
    assert t.status == S.RETRY_SCHEDULED.value and "lease expired" in t.error["message"]


def test_task_with_started_external_action_is_never_auto_retried(db, system, owner):
    t = tasks.create(db, system, title="upload", task_type="youtube.upload", review_required=False)
    tasks.claim_next(db, system, "w", {"youtube.upload"})
    tasks.mark_external_action_started(db, t.id, "w", {"operation": "videos.insert"})
    t = db.get(Task, t.id)
    t.lease_expires_at = utcnow() - timedelta(seconds=1)  # the worker crashed mid-upload
    db.flush()
    tasks.recover_stuck(db, system)
    db.refresh(t)
    assert t.status == S.BLOCKED.value and "will not be retried automatically" in t.blocked_reason
    assert tasks.claim_next(db, system, "w", {"youtube.upload"}, now=utcnow() + timedelta(days=1)) is None
    with pytest.raises(PermissionDenied):
        tasks.unblock(db, system, t.id, "just retry")
    tasks.unblock(db, owner, t.id, "verified on YouTube that nothing was uploaded")
    assert db.get(Task, t.id).status == S.QUEUED.value


def test_idempotency_key_prevents_duplicate_tasks(db, system):
    a = tasks.create(db, system, title="upload video 7", task_type="t", review_required=False, idempotency_key="upload:7")
    b = tasks.create(db, system, title="upload video 7 again", task_type="t", review_required=False, idempotency_key="upload:7")
    assert a.id == b.id


def test_double_claim_is_impossible(db, system):
    tasks.create(db, system, title="only one", task_type="t", review_required=False)
    assert tasks.claim_next(db, system, "w1", {"t"}) is not None
    assert tasks.claim_next(db, system, "w2", {"t"}) is None


def test_concurrency_limit_is_respected(db, system):
    for i in range(5):
        tasks.create(db, system, title=f"t{i}", task_type="t", review_required=False)
    claimed = [tasks.claim_next(db, system, f"w{i}", {"t"}) for i in range(5)]
    assert sum(c is not None for c in claimed) == 2  # default max_concurrent_tasks


def test_priority_orders_the_queue(db, system):
    low = tasks.create(db, system, title="low", task_type="t", review_required=False, priority=90)
    high = tasks.create(db, system, title="high", task_type="t", review_required=False, priority=10)
    assert tasks.claim_next(db, system, "w", {"t"}).id == high.id
    assert low.id != high.id


def test_cancelling_a_running_task_discards_late_results(db, system, owner):
    t = tasks.create(db, system, title="x", task_type="t", review_required=False)
    tasks.claim_next(db, system, "w", {"t"})
    tasks.cancel(db, owner, t.id, "owner changed their mind")
    assert tasks.finish_run(db, system, t.id, "w", result={"late": True}) is None
    assert db.get(Task, t.id).status == S.CANCELLED.value


def test_cannot_cancel_finished_tasks(db, system, owner):
    t = tasks.create(db, system, title="x", task_type="t", review_required=False)
    tasks.claim_next(db, system, "w", {"t"})
    tasks.finish_run(db, system, t.id, "w", result={})
    with pytest.raises(tasks.InvalidTransition):
        tasks.cancel(db, owner, t.id, "late")


# --- pause, budget, approval -----------------------------------------------------------------------------


def test_emergency_pause_stops_new_tasks_and_releases_running_ones(db, system, owner):
    t = tasks.create(db, system, title="x", task_type="t", review_required=False)
    control.pause(db, owner, "stop everything")
    assert tasks.claim_next(db, system, "w", {"t"}) is None
    control.resume(db, owner)
    claimed = tasks.claim_next(db, system, "w", {"t"})
    assert claimed.attempts == 1
    tasks.release_for_pause(db, t.id, "w")
    db.refresh(t)
    assert t.status == S.QUEUED.value and t.attempts == 0  # a paused attempt does not count


def test_manual_begin_refused_while_paused(db, agent, owner):
    pm, w = agent("production-manager"), agent("script-lyrics-writer")
    t = tasks.create(db, pm, title="s", assigned_agent_slug="script-lyrics-writer")
    control.pause(db, owner, "stop")
    with pytest.raises(control.ExternalActionBlocked):
        tasks.begin(db, w, t.id)


def test_paid_task_is_blocked_when_it_does_not_fit_the_budget(db, system, owner):
    t = tasks.create(db, system, title="render", task_type="t", review_required=False,
                     estimated_cost_micro_usd=finance.usd_to_micro(2.0))
    assert tasks.claim_next(db, system, "w", {"t"}) is None  # default limits are $0
    db.refresh(t)
    assert t.status == S.BLOCKED.value and "budget" in t.blocked_reason.lower()


def test_paid_task_runs_within_budget_but_retry_cannot_exceed_it(db, system, owner):
    settings_svc.set_value(db, owner, "budget", {"daily_usd": 3, "monthly_usd": 30, "daily_provider_requests": 50})
    t = tasks.create(db, system, title="render", task_type="t", review_required=False,
                     estimated_cost_micro_usd=finance.usd_to_micro(2.0))
    now = utcnow()
    assert tasks.claim_next(db, system, "w", {"t"}, now=now) is not None
    # first attempt spent $2 before failing; a retry would need another $2 → exceeds the $3 daily cap
    finance.record_usage(db, system, provider="img", operation="gen", cost_micro_usd=finance.usd_to_micro(2.0),
                         cost_kind=finance.CostKind.ACTUAL, task_id=t.id)
    tasks.fail_run(db, system, t.id, "w", "provider timeout", now=now)
    assert tasks.claim_next(db, system, "w", {"t"}, now=now + timedelta(hours=1)) is None
    assert db.get(Task, t.id).status == S.BLOCKED.value


def test_task_waiting_for_owner_approval(db, system, owner, agent):
    t = tasks.create(db, system, title="High-impact op", task_type="t", review_required=False)
    req = tasks.require_approval(db, system, t, kind=ApprovalKind.UNUSUAL_ACTION, summary="s", why="w",
                                 if_approved="runs", if_rejected="cancelled")
    db.refresh(t)
    assert t.status == S.WAITING_FOR_APPROVAL.value
    assert tasks.claim_next(db, system, "w", {"t"}) is None
    with pytest.raises(PermissionDenied):
        approvals.decide(db, agent("ceo"), req.id, approve=True)  # the CEO cannot approve for the owner
    approvals.decide(db, owner, req.id, approve=True)
    db.refresh(t)
    assert t.status == S.QUEUED.value
    assert tasks.claim_next(db, system, "w", {"t"}).id == t.id


def test_owner_rejection_cancels_the_task(db, system, owner):
    t = tasks.create(db, system, title="x", task_type="t", review_required=False)
    req = tasks.require_approval(db, system, t, kind=ApprovalKind.UNUSUAL_ACTION, summary="s", why="w",
                                 if_approved="runs", if_rejected="cancelled")
    approvals.decide(db, owner, req.id, approve=False, note="no")
    assert db.get(Task, t.id).status == S.CANCELLED.value
