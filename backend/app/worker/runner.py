"""Persistent queue runner.

DB transactions are short: claim → (commit) → run the handler with no open transaction → record the
outcome. Long operations (renders, provider calls) therefore never hold the database lock.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.db import session_scope
from app.models.org import Channel
from app.models.tasks import Task
from app.security.permissions import Actor
from app.services import approvals, control, finance, tasks
from app.services import settings as settings_svc

log = logging.getLogger("worker")


class PausedError(Exception):
    """Raised inside a handler when an emergency pause was requested; the task is handed back."""


class NonRetryableError(Exception):
    """The failure cannot be fixed by running again."""


@dataclass
class HandlerResult:
    result: dict[str, Any]
    output_refs: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    actual_cost_micro_usd: int | None = None


@dataclass
class TaskContext:
    task_id: int
    worker_id: str
    task_type: str
    input_refs: dict[str, Any]
    channel_slug: str | None
    video_id: int | None
    assigned_agent: str | None

    def heartbeat(self) -> None:
        with session_scope() as db:
            tasks.heartbeat(db, self.task_id, self.worker_id)

    def check_paused(self) -> None:
        """Handlers call this between steps so an emergency pause can stop long work safely."""
        with session_scope(write=False) as db:
            if settings_svc.is_emergency_paused(db):
                raise PausedError()

    def mark_external_action_started(self, detail: dict[str, Any]) -> None:
        """Call immediately before an irreversible provider call (upload, paid generation, reply)."""
        with session_scope() as db:
            tasks.mark_external_action_started(db, self.task_id, self.worker_id, detail)


Handler = Callable[[TaskContext], HandlerResult]
HANDLERS: dict[str, Handler] = {}


def handler(task_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[task_type] = fn
        return fn

    return deco


class Runner:
    def __init__(self, worker_id: str | None = None, handlers: dict[str, Handler] | None = None):
        self.worker_id = worker_id or f"worker-{socket.gethostname()}-{os.getpid()}"
        self.handlers = handlers if handlers is not None else HANDLERS
        self.actor = Actor.system(self.worker_id)

    def run_once(self, now: datetime | None = None) -> int | None:
        """Run at most one task. Returns the task id, or None when nothing was runnable."""
        with session_scope() as db:
            tasks.recover_stuck(db, self.actor, now)
            approvals.expire_due(db, self.actor)
            task = tasks.claim_next(db, self.actor, self.worker_id, set(self.handlers), now)
            if task is None:
                return None
            ch = db.get(Channel, task.channel_id) if task.channel_id else None
            agent = task.assigned_agent.slug if task.assigned_agent else None
            ctx = TaskContext(task.id, self.worker_id, task.task_type, dict(task.input_refs), ch.slug if ch else None,
                              task.video_id, agent)
            est = task.estimated_cost_micro_usd
        self._execute(ctx, est)
        return ctx.task_id

    def _execute(self, ctx: TaskContext, est_cost: int) -> None:
        fn = self.handlers[ctx.task_type]
        try:
            out = fn(ctx)
        except PausedError:
            with session_scope() as db:
                tasks.release_for_pause(db, ctx.task_id, self.worker_id)
            log.info("task %s released: emergency pause", ctx.task_id)
            return
        except (finance.BudgetExceeded, control.ExternalActionBlocked) as e:
            with session_scope() as db:
                tasks.block_run(db, self.actor, ctx.task_id, self.worker_id,
                                f"{getattr(e, 'code', 'BLOCKED')}: {getattr(e, 'message', None) or getattr(e, 'reason', str(e))}")
            return
        except NonRetryableError as e:
            with session_scope() as db:
                tasks.fail_run(db, self.actor, ctx.task_id, self.worker_id, e, retryable=False)
            return
        except Exception as e:  # noqa: BLE001 - a handler failure must never kill the worker
            log.exception("task %s failed", ctx.task_id)
            with session_scope() as db:
                tasks.fail_run(db, self.actor, ctx.task_id, self.worker_id, e, retryable=True)
            return
        with session_scope() as db:
            tasks.finish_run(db, self.actor, ctx.task_id, self.worker_id, result=out.result,
                             output_refs=out.output_refs, evidence_specs=out.evidence,
                             actual_cost_micro_usd=out.actual_cost_micro_usd)

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        poll = get_settings().worker_poll_seconds
        log.info("worker %s started (poll %.1fs)", self.worker_id, poll)
        while not stop.is_set():
            try:
                ran = self.run_once()
            except Exception:  # noqa: BLE001 - keep the loop alive; the error is logged
                log.exception("runner loop error")
                ran = None
            if ran is None:
                stop.wait(poll)
        log.info("worker %s stopped", self.worker_id)


def pending_count() -> int:
    with session_scope(write=False) as db:
        from sqlalchemy import func, select

        return db.scalar(select(func.count()).select_from(Task).where(Task.status.in_(tasks.CLAIMABLE))) or 0


def _sleep(seconds: float) -> None:  # indirection so tests can avoid real sleeping
    time.sleep(seconds)
