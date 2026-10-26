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
from app.providers.base import ProviderUnavailable
from app.security.permissions import Actor
from app.services import approvals, control, finance, tasks
from app.services import settings as settings_svc

log = logging.getLogger("worker")


class PausedError(Exception):
    """Raised inside a handler when an emergency pause was requested; the task is handed back."""


class NonRetryableError(Exception):
    """The failure cannot be fixed by running again."""


class BlockedError(Exception):
    """The task must wait for something a person (or another step) has to provide. It is parked, not failed or retried."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


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
    lessons: list[str] = field(default_factory=list)  # board entries in force when this task started (public ids)

    def heartbeat(self) -> None:
        from app.ops import health

        with session_scope() as db:
            tasks.heartbeat(db, self.task_id, self.worker_id)
            health.beat(db, self.worker_id, self.task_id)

    def check_paused(self) -> None:
        """Handlers call this between steps so an emergency pause can stop long work safely."""
        with session_scope(write=False) as db:
            if settings_svc.is_emergency_paused(db):
                raise PausedError()

    def mark_external_action_started(self, detail: dict[str, Any]) -> None:
        """Call immediately before an irreversible provider call (upload, paid generation, reply)."""
        with session_scope() as db:
            tasks.mark_external_action_started(db, self.task_id, self.worker_id, detail)

    def clear_external_action_started(self) -> None:
        """Call when the provider refused the request outright, so nothing was generated or charged."""
        with session_scope() as db:
            tasks.clear_external_action_started(db, self.task_id, self.worker_id)


Handler = Callable[[TaskContext], HandlerResult]
HOUSEKEEPING_SECONDS = 15  # heartbeat and scheduler tick; the stale-worker threshold is two minutes
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
                              task.video_id, agent, list(task.lessons_applied or []))
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
        except (finance.BudgetExceeded, control.ExternalActionBlocked, ProviderUnavailable, BlockedError) as e:
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
            tasks.finish_run(db, self.actor, ctx.task_id, self.worker_id,
                             result={**out.result, "lessons_applied": ctx.lessons} if ctx.lessons else out.result,
                             output_refs=out.output_refs, evidence_specs=out.evidence,
                             actual_cost_micro_usd=out.actual_cost_micro_usd)

    def housekeeping(self, *, started: bool = False, now: datetime | None = None) -> list[str]:
        """Beat the heartbeat and let the scheduler create any tasks that are due. Never raises: the loop must keep running."""
        from app.ai import autopilot
        from app.ops import health, schedule

        try:
            with session_scope() as db:
                health.beat(db, self.worker_id, started=started)
                due = schedule.tick(db, now)
        except Exception:  # noqa: BLE001 - a failing scheduler must not stop the queue, and the failure is logged
            log.exception("housekeeping failed")
            due = []
        autopilot.step(self.worker_id)  # never raises; does nothing for work the owner did not start with the AI company
        return due

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        poll = get_settings().worker_poll_seconds
        log.info("worker %s started (poll %.1fs)", self.worker_id, poll)
        self.housekeeping(started=True)
        last_housekeeping = time.monotonic()
        while not stop.is_set():
            try:
                ran = self.run_once()
            except Exception:  # noqa: BLE001 - keep the loop alive; the error is logged
                log.exception("runner loop error")
                ran = None
            if time.monotonic() - last_housekeeping >= HOUSEKEEPING_SECONDS:
                self.housekeeping()
                last_housekeeping = time.monotonic()
            if ran is None:
                stop.wait(poll)
        log.info("worker %s stopped", self.worker_id)
