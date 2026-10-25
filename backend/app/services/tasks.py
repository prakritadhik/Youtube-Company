"""Persistent task queue and the worker → manager verification workflow.

Key rules (covered by tests):
* A worker can only *submit*; the assigned agent's superior verifies the evidence and completes it.
* ``COMPLETED`` without review is only possible for system tasks that have no assigned agent.
* Retries are bounded, back off exponentially and re-check the budget.
* A task whose external action already started is never retried automatically after a crash.
* Dependencies cannot form cycles; failed or cancelled dependencies block their dependents.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.enums import TERMINAL_TASK_STATUSES, ApprovalKind, ApprovalStatus, EvidenceKind, TaskStatus
from app.models.approvals import ApprovalRequest
from app.models.org import Agent, Channel
from app.models.tasks import Evidence, Task, TaskDependency
from app.security.permissions import Actor, P, PermissionDenied
from app.security.redaction import redact_text
from app.services import approvals, audit, board, control, evidence, finance, org
from app.services import settings as settings_svc
from app.timeutil import iso, utcnow

TS = TaskStatus
CLAIMABLE = (TS.QUEUED.value, TS.RETRY_SCHEDULED.value, TS.REVISION_REQUESTED.value)
MAX_REVISIONS = 5


class TaskError(Exception):
    pass


class InvalidTransition(TaskError):
    pass


# --- creation ----------------------------------------------------------------------------------------


def _can_assign(db: Session, actor: Actor, assignee: Agent) -> bool:
    if actor.is_owner or actor.kind.value == "system":
        return True
    if actor.agent_id is None:
        return False
    return actor.agent_id == assignee.id or org.is_superior_of(db, actor, assignee)


def create(
    db: Session,
    actor: Actor,
    *,
    title: str,
    description: str = "",
    task_type: str = "manual",
    channel_slug: str | None = None,
    video_id: int | None = None,
    assigned_agent_slug: str | None = None,
    parent_task_id: int | None = None,
    depends_on: list[int] | None = None,
    priority: int = 50,
    input_refs: dict[str, Any] | None = None,
    review_required: bool = True,
    max_attempts: int | None = None,
    idempotency_key: str | None = None,
    due_at: datetime | None = None,
    estimated_cost_micro_usd: int = 0,
    queue: bool = True,
) -> Task:
    actor.require(P.TASK_CREATE)
    if not title.strip():
        raise TaskError("a task needs a title")

    if idempotency_key:
        existing = db.scalar(select(Task).where(Task.idempotency_key == idempotency_key))
        if existing is not None:
            return existing  # same external intent never creates a second task

    agent = org.get_agent(db, assigned_agent_slug) if assigned_agent_slug else None
    if agent is not None and not _can_assign(db, actor, agent):
        raise PermissionDenied(actor, f"assign:{agent.slug}")
    if agent is None and review_required is False:
        pass  # system task: allowed
    elif agent is not None and review_required is False:
        raise TaskError("tasks assigned to an agent must be reviewed by their manager")

    channel_id = None
    if channel_slug:
        ch = db.scalar(select(Channel).where(Channel.slug == channel_slug))
        if ch is None:
            raise TaskError(f"unknown channel '{channel_slug}'")
        channel_id = ch.id

    cfg_attempts = int(settings_svc.get(db, "budget")["max_task_attempts"])
    task = Task(
        title=title.strip(), description=description, task_type=task_type, status=TS.CREATED.value,
        priority=priority, channel_id=channel_id, video_id=video_id,
        department_id=agent.department_id if agent else None, assigned_agent_id=agent.id if agent else None,
        parent_task_id=parent_task_id, created_by=actor.label, input_refs=input_refs or {},
        review_required=review_required, idempotency_key=idempotency_key, due_at=due_at,
        max_attempts=min(max_attempts or get_settings().task_max_attempts, max(cfg_attempts, 1)),
        estimated_cost_micro_usd=estimated_cost_micro_usd,
    )
    db.add(task)
    db.flush()
    for dep_id in depends_on or []:
        _add_dependency(db, task, dep_id)
    audit.record(db, actor, "task.created", "task", task.id, {"title": task.title, "type": task_type,
                                                                "assignee": assigned_agent_slug})
    if queue:
        _settle(db, task)
    return task


def _add_dependency(db: Session, task: Task, dep_id: int) -> None:
    if dep_id == task.id:
        raise TaskError("a task cannot depend on itself")
    dep = db.get(Task, dep_id)
    if dep is None:
        raise TaskError(f"dependency {dep_id} does not exist")
    # cycle check: would dep (transitively) depend on task?
    stack, seen = [dep_id], set()
    while stack:
        cur = stack.pop()
        if cur == task.id:
            raise TaskError("dependency would create a cycle")
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == cur)))
    if db.get(TaskDependency, (task.id, dep_id)) is None:
        db.add(TaskDependency(task_id=task.id, depends_on_id=dep_id))
        db.flush()


def add_dependency(db: Session, actor: Actor, task_id: int, depends_on_id: int) -> None:
    actor.require(P.TASK_ASSIGN if actor.agent_id else P.TASK_CREATE)
    task = _get(db, task_id)
    if task.status in {s.value for s in TERMINAL_TASK_STATUSES} or task.status == TS.RUNNING.value:
        raise TaskError("dependencies can only be added to tasks that have not started")
    _add_dependency(db, task, depends_on_id)
    if task.status in (TS.QUEUED.value, TS.CREATED.value):
        _settle(db, task)


def dependencies_of(db: Session, task_id: int) -> list[int]:
    return list(db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task_id)))


def dependents_of(db: Session, task_id: int) -> list[int]:
    return list(db.scalars(select(TaskDependency.task_id).where(TaskDependency.depends_on_id == task_id)))


def _get(db: Session, task_id: int) -> Task:
    t = db.get(Task, task_id)
    if t is None:
        raise TaskError("task not found")
    return t


def _set_status(db: Session, task: Task, to: TS, *, allowed_from: tuple[str, ...] | None = None) -> None:
    if allowed_from is not None and task.status not in allowed_from:
        raise InvalidTransition(f"task {task.id} is {task.status}; cannot move to {to.value}")
    task.status = to.value
    task.updated_at = utcnow()
    db.flush()


def _approval_state(db: Session, task: Task) -> str | None:
    if not task.requires_approval:
        return None
    if task.approval_request_id is None:
        return ApprovalStatus.PENDING.value
    req = db.get(ApprovalRequest, task.approval_request_id)
    return req.status if req else ApprovalStatus.PENDING.value


def _settle(db: Session, task: Task) -> None:
    """Decide whether a freshly created/unblocked task waits or is queued."""
    if task.status in {s.value for s in TERMINAL_TASK_STATUSES} or task.status == TS.RUNNING.value:
        return
    deps = [db.get(Task, d) for d in dependencies_of(db, task.id)]
    if any(d.status in (TS.FAILED.value, TS.CANCELLED.value) for d in deps if d):
        task.blocked_reason = "a dependency failed or was cancelled"
        _set_status(db, task, TS.BLOCKED)
    elif any(d.status != TS.COMPLETED.value for d in deps if d):
        _set_status(db, task, TS.WAITING_FOR_DEPENDENCY)
    elif _approval_state(db, task) not in (None, ApprovalStatus.APPROVED.value):
        if _approval_state(db, task) in (ApprovalStatus.REJECTED.value, ApprovalStatus.CANCELLED.value):
            task.cancel_reason = "approval was not granted"
            _set_status(db, task, TS.CANCELLED)
        else:
            _set_status(db, task, TS.WAITING_FOR_APPROVAL)
    else:
        task.next_run_at = task.next_run_at or utcnow()
        _set_status(db, task, TS.QUEUED)


def require_approval(db: Session, actor: Actor, task: Task, *, kind: ApprovalKind, summary: str, why: str,
                     if_approved: str, if_rejected: str) -> ApprovalRequest:
    """Hold a task until the owner approves it."""
    task.requires_approval = True
    req = approvals.request(
        db, actor, kind=kind, title=task.title, summary=summary, why_it_matters=why, if_approved=if_approved,
        if_rejected=if_rejected, channel_id=task.channel_id, subject_type="task", subject_id=task.id,
        action={"handler": "task.approved", "args": {"task_id": task.id}},
    )
    task.approval_request_id = req.id
    _settle(db, task)
    return req


@approvals.register_handler("task.approved")
def _on_task_approved(db: Session, owner: Actor, req: ApprovalRequest, args: dict[str, Any]) -> dict[str, Any]:
    task = _get(db, args["task_id"])
    req.status = ApprovalStatus.APPROVED.value  # visible to _settle within this transaction
    if task.status == TS.WAITING_FOR_APPROVAL.value:
        _set_status(db, task, TS.QUEUED)
    return {"task_id": task.id, "status": task.status}


@approvals.register_handler("task.approved:rejected")
def _on_task_rejected(db: Session, owner: Actor, req: ApprovalRequest, args: dict[str, Any]) -> dict[str, Any]:
    task = _get(db, args["task_id"])
    if task.status not in {s.value for s in TERMINAL_TASK_STATUSES}:
        task.cancel_reason = "owner rejected the approval request"
        _set_status(db, task, TS.CANCELLED)
        _block_dependents(db, task)
    return {"task_id": task.id, "status": task.status}


# --- queue: claim / run / finish --------------------------------------------------------------------------


def claim_next(
    db: Session, runner: Actor, worker_id: str, task_types: set[str], now: datetime | None = None
) -> Task | None:
    """Atomically claim the next runnable task. Returns None when paused, saturated or empty."""
    runner.require(P.TASK_RUN)
    now = now or utcnow()
    if settings_svc.is_emergency_paused(db):
        return None
    max_conc = int(settings_svc.get(db, "budget")["max_concurrent_tasks"])
    running = len(db.scalars(select(Task.id).where(Task.status == TS.RUNNING.value)).all())
    if running >= min(max_conc, get_settings().max_concurrent_tasks):
        return None

    candidates = db.scalars(
        select(Task)
        .where(Task.status.in_(CLAIMABLE), Task.task_type.in_(task_types),
               (Task.next_run_at.is_(None)) | (Task.next_run_at <= now))
        .order_by(Task.priority, Task.id)
        .limit(25)
    ).all()
    for task in candidates:
        deps = [db.get(Task, d) for d in dependencies_of(db, task.id)]
        if any(d.status in (TS.FAILED.value, TS.CANCELLED.value) for d in deps if d):
            task.blocked_reason = "a dependency failed or was cancelled"
            _set_status(db, task, TS.BLOCKED)
            continue
        if any(d.status != TS.COMPLETED.value for d in deps if d):
            _set_status(db, task, TS.WAITING_FOR_DEPENDENCY)
            continue
        if _approval_state(db, task) not in (None, ApprovalStatus.APPROVED.value):
            _set_status(db, task, TS.WAITING_FOR_APPROVAL)
            continue
        if task.estimated_cost_micro_usd > 0:
            try:
                finance.authorize(db, estimated_micro_usd=task.estimated_cost_micro_usd, video_id=task.video_id)
            except (finance.BudgetExceeded, control.ExternalActionBlocked) as e:
                task.blocked_reason = f"budget/safety: {getattr(e, 'message', None) or getattr(e, 'reason', str(e))}"
                _set_status(db, task, TS.BLOCKED)
                audit.record(db, runner, "task.blocked_budget", "task", task.id, {"reason": task.blocked_reason})
                continue
        lease = now + timedelta(seconds=get_settings().task_lease_seconds)
        res = db.execute(
            update(Task)
            .where(Task.id == task.id, Task.status.in_(CLAIMABLE))
            .values(status=TS.RUNNING.value, locked_by=worker_id, lease_expires_at=lease, heartbeat_at=now,
                    attempts=Task.attempts + 1, started_at=task.started_at or now, updated_at=now)
        )
        if res.rowcount != 1:
            continue
        db.refresh(task)
        _record_board_use(db, task)
        audit.record(db, runner, "task.started", "task", task.id, {"worker": worker_id, "attempt": task.attempts})
        return task
    return None


def _record_board_use(db: Session, task: Task) -> None:
    """The role loads the current instructions and acknowledges them before the task proceeds."""
    if task.assigned_agent_id is None:
        return
    agent = db.get(Agent, task.assigned_agent_id)
    channel = db.get(Channel, task.channel_id) if task.channel_id else None
    info = board.acknowledge(db, org.actor_for(agent), channel.slug if channel else None, task.id)  # type: ignore[arg-type]
    task.board_version_used = info["release_version"]
    task.board_digest_used = info["digest"]


def begin(db: Session, actor: Actor, task_id: int) -> Task:
    """Start a task manually as its assigned agent. Requires the current board to be acknowledged."""
    task = _get(db, task_id)
    if actor.agent_id is None or actor.agent_id != task.assigned_agent_id:
        raise PermissionDenied(actor, "task:begin")
    control.assert_can_start_work(db)
    if task.status not in CLAIMABLE:
        raise InvalidTransition(f"task {task.id} is {task.status}; cannot begin")
    channel = db.get(Channel, task.channel_id) if task.channel_id else None
    info = board.assert_acknowledged(db, actor.agent_id, channel.slug if channel else None)
    task.board_version_used = info["release_version"]
    task.board_digest_used = info["digest"]
    task.attempts += 1
    task.started_at = task.started_at or utcnow()
    task.locked_by = actor.label
    task.lease_expires_at = utcnow() + timedelta(seconds=get_settings().task_lease_seconds)
    _set_status(db, task, TS.RUNNING)
    audit.record(db, actor, "task.started", "task", task.id, {"attempt": task.attempts})
    return task


def heartbeat(db: Session, task_id: int, worker_id: str) -> bool:
    task = _get(db, task_id)
    if task.status != TS.RUNNING.value or task.locked_by != worker_id:
        return False
    now = utcnow()
    task.heartbeat_at = now
    task.lease_expires_at = now + timedelta(seconds=get_settings().task_lease_seconds)
    db.flush()
    return True


def mark_external_action_started(db: Session, task_id: int, worker_id: str, detail: dict[str, Any]) -> None:
    """Persist (immediately) that an irreversible external call began, so recovery never repeats it."""
    task = _get(db, task_id)
    if task.status != TS.RUNNING.value or task.locked_by != worker_id:
        raise TaskError("task is not running under this worker")
    task.output_refs = {**task.output_refs, "external_action_started": {"at": iso(utcnow()), **detail}}
    db.flush()


def _attach_evidence(db: Session, actor: Actor, task: Task, specs: list[dict[str, Any]]) -> None:
    for spec in specs:
        evidence.add(
            db, actor, task_id=task.id, kind=EvidenceKind(spec["kind"]), ref=spec["ref"],
            note=spec.get("note", ""), meta=spec.get("meta"),
        )


def finish_run(
    db: Session,
    actor: Actor,
    task_id: int,
    worker_id: str,
    *,
    result: dict[str, Any],
    output_refs: dict[str, Any] | None = None,
    evidence_specs: list[dict[str, Any]] | None = None,
    actual_cost_micro_usd: int | None = None,
) -> Task | None:
    """Worker/handler reports success. The task is SUBMITTED for review (or COMPLETED if system-only)."""
    task = _get(db, task_id)
    if task.status != TS.RUNNING.value or task.locked_by != worker_id:
        return None  # cancelled or re-claimed while running: discard silently
    _attach_evidence(db, actor, task, evidence_specs or [])
    task.result = result
    task.output_refs = {**task.output_refs, **(output_refs or {})}
    if actual_cost_micro_usd is not None:
        task.actual_cost_micro_usd = actual_cost_micro_usd
    task.locked_by = None
    task.lease_expires_at = None
    task.error = None
    now = utcnow()
    if task.review_required:
        task.submitted_at = now
        _set_status(db, task, TS.SUBMITTED)
        audit.record(db, actor, "task.submitted", "task", task.id, {"evidence": len(evidence_specs or [])})
    else:
        if task.assigned_agent_id is not None:
            raise TaskError("agent tasks must be reviewed by their manager")
        task.completed_at = now
        _set_status(db, task, TS.COMPLETED)
        audit.record(db, actor, "task.completed", "task", task.id, {"reviewed": False})
        _promote_dependents(db, task)
    return task


def submit(db: Session, actor: Actor, task_id: int, *, result: dict[str, Any],
           evidence_specs: list[dict[str, Any]] | None = None) -> Task:
    """An agent submits its own running task for manager review."""
    task = _get(db, task_id)
    if actor.agent_id is None or actor.agent_id != task.assigned_agent_id:
        raise PermissionDenied(actor, "task:submit")
    out = finish_run(db, actor, task_id, actor.label, result=result, evidence_specs=evidence_specs)
    if out is None:
        raise InvalidTransition("task is not running under this agent")
    return out


def fail_run(db: Session, actor: Actor, task_id: int, worker_id: str, error: BaseException | str, *,
             retryable: bool = True, now: datetime | None = None) -> Task | None:
    task = _get(db, task_id)
    if task.status != TS.RUNNING.value or task.locked_by != worker_id:
        return None
    return _fail(db, actor, task, error, retryable=retryable, now=now)


def _fail(db: Session, actor: Actor, task: Task, error: BaseException | str, *, retryable: bool,
          now: datetime | None = None) -> Task:
    now = now or utcnow()
    msg = redact_text(str(error))[:2000]
    tb = redact_text("".join(traceback.format_exception(error))[-4000:]) if isinstance(error, BaseException) else None
    task.error = {"message": msg, "traceback": tb, "attempt": task.attempts, "at": iso(now)}
    task.locked_by = None
    task.lease_expires_at = None
    started = task.output_refs.get("external_action_started")
    if started:
        task.blocked_reason = ("An external action had already started when this task stopped; it will not be "
                               "retried automatically. Verify the provider's state before re-running.")
        _set_status(db, task, TS.BLOCKED)
        audit.record(db, actor, "task.blocked_unsafe_retry", "task", task.id, {"external": started})
        return task
    cfg_attempts = int(settings_svc.get(db, "budget")["max_task_attempts"])
    limit = min(task.max_attempts, max(cfg_attempts, 1))
    if retryable and task.attempts < limit:
        s = get_settings()
        delay = min(s.task_backoff_cap_seconds, s.task_backoff_base_seconds * (2 ** (task.attempts - 1)))
        task.next_run_at = now + timedelta(seconds=delay)
        _set_status(db, task, TS.RETRY_SCHEDULED)
        audit.record(db, actor, "task.retry_scheduled", "task", task.id, {"attempt": task.attempts, "delay_s": delay})
    else:
        _set_status(db, task, TS.FAILED)
        audit.record(db, actor, "task.failed", "task", task.id, {"attempts": task.attempts, "error": msg[:300]})
        _block_dependents(db, task)
    return task


def block_run(db: Session, actor: Actor, task_id: int, worker_id: str, reason: str) -> Task | None:
    """A running task cannot continue for a policy reason (budget, safety). Preserves its state."""
    task = _get(db, task_id)
    if task.status != TS.RUNNING.value or task.locked_by != worker_id:
        return None
    task.blocked_reason = reason
    task.locked_by = None
    task.lease_expires_at = None
    _set_status(db, task, TS.BLOCKED)
    audit.record(db, actor, "task.blocked", "task", task.id, {"reason": reason})
    return task


def release_for_pause(db: Session, task_id: int, worker_id: str) -> None:
    """A running task noticed an emergency pause: hand it back without consuming an attempt."""
    task = _get(db, task_id)
    if task.status == TS.RUNNING.value and task.locked_by == worker_id:
        task.attempts = max(task.attempts - 1, 0)
        task.locked_by = None
        task.lease_expires_at = None
        _set_status(db, task, TS.QUEUED)


def recover_stuck(db: Session, actor: Actor, now: datetime | None = None) -> list[int]:
    """Tasks whose lease expired (crash, restart) are failed/retried — never blindly repeated if external."""
    now = now or utcnow()
    stuck = db.scalars(
        select(Task).where(Task.status == TS.RUNNING.value, Task.lease_expires_at.is_not(None),
                           Task.lease_expires_at < now)
    ).all()
    for t in stuck:
        _fail(db, actor, t, "lease expired: worker stopped responding or the application restarted",
              retryable=True, now=now)
        audit.record(db, actor, "task.recovered", "task", t.id, {"now_status": t.status})
    return [t.id for t in stuck]


# --- review (manager) -----------------------------------------------------------------------------------


def review(db: Session, actor: Actor, task_id: int, *, approve: bool, notes: str = "") -> Task:
    """The assigned agent's superior verifies the evidence and accepts or returns the work."""
    actor.require(P.TASK_REVIEW)
    task = _get(db, task_id)
    if task.status != TS.SUBMITTED.value:
        raise InvalidTransition(f"task {task.id} is {task.status}, not SUBMITTED")
    if task.assigned_agent_id is None:
        raise TaskError("task has no assignee to review")
    assignee = db.get(Agent, task.assigned_agent_id)
    if not org.is_superior_of(db, actor, assignee):  # type: ignore[arg-type]
        raise PermissionDenied(actor, "task:review (not in the reporting line)")

    if not approve:
        if not notes.strip():
            raise TaskError("revision notes are required")
        task.revision_count += 1
        task.review_notes = notes
        if task.revision_count > MAX_REVISIONS:
            task.blocked_reason = f"returned {task.revision_count} times; needs the COO's attention"
            _set_status(db, task, TS.BLOCKED)
        else:
            _set_status(db, task, TS.REVISION_REQUESTED)
        audit.record(db, actor, "task.revision_requested", "task", task.id, {"notes": notes})
        return task

    items = evidence.for_task(db, task.id)
    stats = evidence.verify_all(db, items, actor)
    if stats["failed"] > 0:
        raise TaskError("cannot complete: some evidence failed verification — request a revision")
    if stats["verified"] == 0:
        raise TaskError(
            "cannot complete: no machine-verifiable evidence (file, record, API response or test result) was supplied"
        )
    task.review_notes = notes or "evidence verified"
    task.completed_at = utcnow()
    _set_status(db, task, TS.COMPLETED)
    audit.record(db, actor, "task.completed", "task", task.id, {"reviewed_by": actor.label, "evidence": stats})
    _promote_dependents(db, task)
    return task


def _promote_dependents(db: Session, task: Task) -> None:
    for dep_id in dependents_of(db, task.id):
        dep = db.get(Task, dep_id)
        if dep and dep.status in (TS.WAITING_FOR_DEPENDENCY.value, TS.CREATED.value):
            _settle(db, dep)


def _block_dependents(db: Session, task: Task) -> None:
    for dep_id in dependents_of(db, task.id):
        dep = db.get(Task, dep_id)
        if dep and dep.status not in {s.value for s in TERMINAL_TASK_STATUSES} and dep.status != TS.RUNNING.value:
            dep.blocked_reason = f"dependency task {task.id} is {task.status}"
            _set_status(db, dep, TS.BLOCKED)


# --- control ---------------------------------------------------------------------------------------------


def cancel(db: Session, actor: Actor, task_id: int, reason: str) -> Task:
    actor.require(P.TASK_CANCEL)
    task = _get(db, task_id)
    if task.status in {s.value for s in TERMINAL_TASK_STATUSES}:
        raise InvalidTransition(f"task {task.id} already {task.status}")
    if not reason.strip():
        raise TaskError("a reason is required")
    was_running = task.status == TS.RUNNING.value
    task.cancel_reason = reason
    task.locked_by = None
    task.lease_expires_at = None
    _set_status(db, task, TS.CANCELLED)
    audit.record(db, actor, "task.cancelled", "task", task.id, {"reason": reason, "was_running": was_running})
    _block_dependents(db, task)
    return task


def unblock(db: Session, actor: Actor, task_id: int, note: str) -> Task:
    """A manager (or above) resolves a blocker and returns the task to the queue."""
    actor.require(P.TASK_ASSIGN if actor.agent_id else P.TASK_CANCEL)
    task = _get(db, task_id)
    if task.status != TS.BLOCKED.value:
        raise InvalidTransition(f"task {task.id} is not BLOCKED")
    if task.output_refs.get("external_action_started") and not actor.is_owner:
        raise PermissionDenied(actor, "task:unblock (external action started; the owner must reconcile)")
    task.blocked_reason = None
    task.output_refs = {k: v for k, v in task.output_refs.items() if k != "external_action_started"}
    audit.record(db, actor, "task.unblocked", "task", task.id, {"note": note})
    _settle(db, task)
    return task


# --- serialisation ----------------------------------------------------------------------------------------


def summary(db: Session, t: Task) -> dict[str, Any]:
    """Owner-facing view: no raw results, errors or tracebacks."""
    agent = db.get(Agent, t.assigned_agent_id) if t.assigned_agent_id else None
    ch = db.get(Channel, t.channel_id) if t.channel_id else None
    n_ev = len(evidence.for_task(db, t.id))
    return {
        "id": t.id, "title": t.title, "task_type": t.task_type, "status": t.status, "priority": t.priority,
        "channel": ch.slug if ch else None, "owner": agent.name if agent else "System",
        "owner_slug": agent.slug if agent else None, "depends_on": dependencies_of(db, t.id),
        "blocker": t.blocked_reason, "attempts": t.attempts, "evidence_count": n_ev,
        "review_notes": t.review_notes, "created_at": iso(t.created_at), "updated_at": iso(t.updated_at),
        "completed_at": iso(t.completed_at), "due_at": iso(t.due_at),
        "board_version_used": t.board_version_used,
    }


def detail(db: Session, t: Task) -> dict[str, Any]:
    """Diagnostic view (Layer A): includes results, errors and evidence."""
    return {
        **summary(db, t),
        "description": t.description, "input_refs": t.input_refs, "output_refs": t.output_refs,
        "result": t.result, "error": t.error, "created_by": t.created_by, "max_attempts": t.max_attempts,
        "next_run_at": iso(t.next_run_at), "locked_by": t.locked_by, "lease_expires_at": iso(t.lease_expires_at),
        "heartbeat_at": iso(t.heartbeat_at), "idempotency_key": t.idempotency_key,
        "board_digest_used": t.board_digest_used, "review_required": t.review_required,
        "estimated_cost_usd": finance.micro_to_usd(t.estimated_cost_micro_usd),
        "actual_cost_usd": finance.micro_to_usd(t.actual_cost_micro_usd),
        "evidence": [evidence.serialize(e) for e in db.scalars(select(Evidence).where(Evidence.task_id == t.id))],
    }
