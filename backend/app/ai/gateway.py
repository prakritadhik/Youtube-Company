"""The only door to the AI brain.

Every model call in the company goes through ``ask``. It enforces, in this order: the role may use a model at all
(``llm.generate`` tool), the Claude Code provider is ready (installed, owner attested the plan terms, no emergency pause),
the owner's daily request limits allow it, then runs the program with no tools, checks the answer against a schema and the
caller's own rules, and records what happened (a row, a file with the full prompt and answer, a usage record).

A call can fail in three different ways, and the queue treats them differently:

* the brain is not usable *right now* (not signed in, usage limit reached, limit set to zero, paused): ``BrainNotReady``.
  The task is parked with the reason and is not retried; it resumes by itself when the brain is ready again;
* the call itself failed in a way worth repeating (timeout, malformed wrapper): a plain retryable error;
* the answer was unusable even after one correction attempt: ``BrainOutputError`` (retryable, bounded by the task's attempts).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import schema as schema_mod
from app.db import session_scope
from app.enums import CostKind
from app.models.ai import AiGeneration
from app.models.org import Channel
from app.providers import base as providers
from app.providers import claude_code as cc
from app.security.permissions import Actor
from app.security.redaction import redact_text
from app.services import finance, org
from app.services import settings as settings_svc
from app.storage import write_json_artifact
from app.timeutil import iso, utcnow
from app.worker.runner import BlockedError

log = logging.getLogger("ai")

PROVIDER, CAPABILITY = "claude-code", "llm.generate"
SYSTEM = ("You are a staff member of a small media company. You have no tools and no internet access. "
          "Answer with exactly one JSON object and nothing else.")
# Failures that mean "the brain cannot be used until something outside the app changes": wait, do not hammer.
PARKING_CODES = {"NOT_SIGNED_IN", "USAGE_LIMIT", "UNKNOWN_ERROR", "NOT_INSTALLED", "UNSUPPORTED_VERSION"}
COOLDOWN = timedelta(minutes=20)
HEARTBEAT_EVERY_S = 30


class BrainNotReady(BlockedError):
    """The brain cannot be used now. ``code`` always starts with ``AI_`` so work parked for this reason can be resumed together."""

    def __init__(self, reason: str, state: str = "NOT_READY"):
        super().__init__(f"AI_{state}", reason)


class BrainOutputError(RuntimeError):
    """The model answered, but not with anything usable, even after a correction attempt."""


@dataclass
class Answer:
    data: dict[str, Any]
    text: str
    generation_ids: list[int] = field(default_factory=list)
    model: str | None = None
    attempts: int = 1


# --- readiness -----------------------------------------------------------------------------------------------------------


def _cooling_down(db: Session) -> AiGeneration | None:
    last = db.scalar(select(AiGeneration).order_by(AiGeneration.id.desc()).limit(1))
    if last is not None and last.status == "FAILED" and last.error_code in PARKING_CODES and utcnow() - last.created_at < COOLDOWN:
        return last
    return None


def brain_state(db: Session) -> dict[str, Any]:
    """Whether a model call could start now, and if not exactly why. Never calls the model."""
    state, detail = providers.capability_state(db, PROVIDER, CAPABILITY)
    if state != "ready":
        return {"ready": False, "code": state.upper(), "detail": detail}
    b = settings_svc.get(db, "budget")
    if int(b["daily_model_requests"]) <= 0 or int(b["daily_provider_requests"]) <= 0:
        return {"ready": False, "code": "LIMIT_ZERO", "detail": "The daily model-request limits in Settings are 0, which means not permitted. "
                "Set 'Daily model requests' and 'Daily provider requests' above 0 to let the company use the brain."}
    try:
        finance.authorize(db, category="model")
    except finance.BudgetExceeded as e:
        return {"ready": False, "code": "LIMIT_REACHED", "detail": f"{e.message} It resets at the start of the next day."}
    last = _cooling_down(db)
    if last is not None:
        until = last.created_at + COOLDOWN
        return {"ready": False, "code": f"COOLING_DOWN_{last.error_code}",
                "detail": f"The last call failed ({last.error_code}): {last.error_detail}. Work waits until {iso(until)} or until you run the connection test in Settings."}
    return {"ready": True, "code": "READY", "detail": "ready"}


def _preflight(role: str, video_id: int | None, channel_slug: str | None) -> dict[str, Any]:
    with session_scope(write=False) as db:
        actor = org.actor_for_slug(db, role)
        actor.require_tool(CAPABILITY)
        st = brain_state(db)
        if not st["ready"]:
            raise BrainNotReady(st["detail"], st["code"])
        ai = settings_svc.get(db, "ai")
        ch = db.scalar(select(Channel).where(Channel.slug == channel_slug)) if channel_slug else None
        return {"model": ai.get("model") or None, "effort": ai.get("effort") or None, "timeout": float(ai.get("timeout_seconds") or 300),
                "channel_id": ch.id if ch else None, "binary": cc.locate()}


# --- recording -----------------------------------------------------------------------------------------------------------


def _record(*, purpose: str, role: str, task_id: int | None, video_id: int | None, channel_id: int | None, attempt: int, status: str,
            prompt: str, response: str | None, gen: cc.Generation | None, error: cc.ClaudeCodeError | Exception | None,
            error_code: str | None, output: dict[str, Any] | None, problems: Sequence[str] = ()) -> int:
    price = int(round(gen.list_price_usd * 1_000_000)) if gen is not None and gen.list_price_usd is not None else None
    detail = redact_text(str(error))[:600] if error is not None else (("; ".join(problems))[:600] if problems else None)
    with session_scope() as db:
        row = AiGeneration(
            purpose=purpose, role_slug=role, task_id=task_id, video_id=video_id, channel_id=channel_id, attempt=attempt, status=status,
            error_code=error_code, error_detail=detail, model=gen.model if gen else None,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(), prompt_chars=len(prompt),
            response_chars=len(response) if response is not None else None, input_tokens=gen.input_tokens if gen else None,
            output_tokens=gen.output_tokens if gen else None, list_price_micro_usd=price, duration_ms=gen.duration_ms if gen else None,
            output=output,
        )
        db.add(row)
        db.flush()
        day = utcnow().strftime("%Y-%m-%d")
        row.artifact_path = write_json_artifact(f"ai/{day}", f"{row.id}.json", {
            "id": row.id, "purpose": purpose, "role": role, "attempt": attempt, "status": status, "prompt": redact_text(prompt),
            "response": redact_text(response) if response is not None else None, "problems": list(problems), "error": detail,
            "note": "Written by the AI gateway. The prompt is exactly what was sent to Claude Code on this computer."})
        # Every call that reached the program is a request against the owner's plan, whatever came back.
        finance.record_usage(
            db, Actor.system("ai-gateway"), provider=PROVIDER, operation=purpose, category="model", cost_kind=CostKind.INCLUDED,
            cost_micro_usd=price or 0, units=float((gen.input_tokens + gen.output_tokens) if gen else 0), unit_type="tokens",
            video_id=video_id, task_id=task_id, channel_slug=None,
            meta={"generation_id": row.id, "status": status, "role": role, "list_price_estimate": price is not None, "error_code": error_code})
        return row.id


@contextmanager
def _beating(beat: Callable[[], None] | None) -> Iterator[None]:
    """Keep the task's lease (and the worker's health beat) fresh while the program thinks, which can take minutes."""
    if beat is None:
        yield
        return
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(HEARTBEAT_EVERY_S):
            try:
                beat()
            except Exception:  # noqa: BLE001 - a missed beat must never break the call
                log.exception("heartbeat during a model call failed")

    t = threading.Thread(target=loop, daemon=True, name="ai-heartbeat")
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=5)


def _correction(prompt: str, previous: str, problems: Sequence[str]) -> str:
    bullets = "\n".join(f"- {p}" for p in problems[:12])
    return (f"{prompt}\n\n---\nYour previous answer could not be used:\n{bullets}\n\nYour previous answer was:\n{previous[:6000]}\n\n"
            "Answer again with the complete corrected JSON object only, fixing every problem listed.")


# --- the call ------------------------------------------------------------------------------------------------------------


def ask(*, role: str, purpose: str, prompt: str, schema: dict[str, Any], task_id: int | None = None, video_id: int | None = None,
        channel_slug: str | None = None, validators: Sequence[Callable[[dict[str, Any]], list[str]]] = (), max_attempts: int = 2,
        beat: Callable[[], None] | None = None, client_factory: Callable[..., cc.ClaudeCodeClient] | None = None) -> Answer:
    """Ask the brain as ``role``. Returns the checked answer, or raises ``BrainNotReady`` / ``BrainOutputError`` / a retryable error."""
    sent = prompt
    ids: list[int] = []
    last_problems: list[str] = []
    for attempt in range(1, max_attempts + 1):
        cfg = _preflight(role, video_id, channel_slug)
        make = client_factory or cc.ClaudeCodeClient
        client = make(cfg["binary"], model=cfg["model"], effort=cfg["effort"], timeout=cfg["timeout"])
        try:
            with _beating(beat):
                gen = client.generate(system=SYSTEM, prompt=sent)
        except cc.ClaudeCodeError as e:
            short = e.code.removeprefix("CLAUDE_")
            _record(purpose=purpose, role=role, task_id=task_id, video_id=video_id, channel_id=cfg["channel_id"], attempt=attempt, status="FAILED",
                    prompt=sent, response=None, gen=None, error=e, error_code=short, output=None)
            if short in PARKING_CODES:
                raise BrainNotReady(e.message, short) from e
            raise  # timeout / malformed wrapper: retryable, the queue backs off
        data: dict[str, Any] | None = None
        try:
            parsed = schema_mod.extract_json(gen.text)
            problems = schema_mod.validate(schema, parsed)
            if not problems:
                data = parsed
                for check in validators:
                    problems += check(data)
        except schema_mod.NoJson as e:
            problems = [str(e)]
        gid = _record(purpose=purpose, role=role, task_id=task_id, video_id=video_id, channel_id=cfg["channel_id"], attempt=attempt,
                      status="OK" if not problems else "INVALID", prompt=sent, response=gen.text, gen=gen, error=None, error_code=None,
                      output=data if not problems else None, problems=problems)
        ids.append(gid)
        if not problems and data is not None:
            return Answer(data=data, text=gen.text, generation_ids=ids, model=gen.model, attempts=attempt)
        last_problems = problems
        sent = _correction(prompt, gen.text, problems)
    raise BrainOutputError("The model's answer could not be used after correction: " + "; ".join(last_problems[:6]))
