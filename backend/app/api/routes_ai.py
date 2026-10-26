"""The AI brain: its state, the connection test, what it has done, and the two things the owner asks of it (ideas, and a production run)."""

from __future__ import annotations

import json
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import conductor, gateway
from app.api.deps import Auth, require
from app.db import get_db, get_wdb, session_scope
from app.models.ai import AiGeneration
from app.models.finance import UsageRecord
from app.providers import base as providers
from app.providers import claude_code as cc
from app.security.permissions import P
from app.services import finance, org, tasks
from app.services import settings as settings_svc
from app.storage import UnsafePath, resolve_data_path
from app.timeutil import iso, utcnow

router = APIRouter(prefix="/api", tags=["ai"])

_test_lock = threading.Lock()
_test: dict[str, Any] = {"state": "idle", "started_at": None, "report": None}  # the connection test runs in the background: it can take a minute


class IdeasIn(BaseModel):
    channel: str = Field(min_length=3, max_length=40)
    count: int = Field(default=3, ge=1, le=5)


def _row(g: AiGeneration) -> dict[str, Any]:
    return {"id": g.id, "at": iso(g.created_at), "purpose": g.purpose, "role": g.role_slug, "task_id": g.task_id, "video_id": g.video_id, "attempt": g.attempt,
            "status": g.status, "error_code": g.error_code, "error": g.error_detail, "model": g.model, "input_tokens": g.input_tokens, "output_tokens": g.output_tokens,
            "list_price_usd": None if g.list_price_micro_usd is None else g.list_price_micro_usd / 1_000_000, "duration_ms": g.duration_ms}


@router.get("/ai/status")
def status(db: Session = Depends(get_db), auth: Auth = Depends(require(P.SETTINGS_READ))):
    cap_state, cap_detail = providers.capability_state(db, gateway.PROVIDER, gateway.CAPABILITY)
    brain = gateway.brain_state(db)
    b = settings_svc.get(db, "budget")
    used = db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.provider == gateway.PROVIDER, UsageRecord.ts >= finance.day_start())) or 0
    last_test = db.scalar(select(AiGeneration).where(AiGeneration.purpose == "doctor").order_by(AiGeneration.id.desc()).limit(1))
    with _test_lock:
        running = dict(_test)
    return {
        "brain": brain, "capability": {"state": cap_state, "detail": cap_detail}, "settings": settings_svc.get(db, "ai"),
        "program": {"found": cc.locate(), "configured_path": bool(cc.get_settings().claude_code_bin)},
        "allowance": {"daily_model_requests": b["daily_model_requests"], "daily_provider_requests": b["daily_provider_requests"], "used_today": used},
        "attested": bool((settings_svc.get(db, "provider_attestations").get(gateway.PROVIDER) or {}).get("api_access_confirmed")),
        "test": running, "last_test": {**_row(last_test), "report": last_test.output} if last_test else None,
        "notes": ["Each call uses your Claude plan's usage, not an API key. The list-price figures are the program's own estimate, not a bill.",
                  "The brain is Claude Code run with no tools and no internet access; it can only return text, which the company then checks."],
    }


def _run_test() -> None:
    try:
        with session_scope(write=False) as db:
            ai = settings_svc.get(db, "ai")
        client = cc.ClaudeCodeClient(cc.locate(), model=ai.get("model") or None, effort=ai.get("effort") or None)
        report = client.doctor()
    except Exception as e:  # noqa: BLE001 - the test must always finish with an answer the owner can read
        report = {"ok": False, "problems": [f"The test itself failed: {type(e).__name__}: {e}"]}
    code = None
    if not report.get("ok"):
        code = "NOT_SIGNED_IN" if report.get("signed_in") is False else "NOT_INSTALLED" if report.get("installed") is False and report.get("path") is None else "TEST_FAILED"
    with session_scope() as db:
        db.add(AiGeneration(purpose="doctor", role_slug="owner", attempt=1, status="OK" if report.get("ok") else "FAILED", error_code=code,
                            error_detail="; ".join(report.get("problems") or [])[:600] or None, model=report.get("model"), prompt_sha256="0" * 64, prompt_chars=0,
                            duration_ms=report.get("duration_ms"), output=report))
    with _test_lock:
        _test.update(state="done", report=report)


@router.post("/ai/test")
def start_test(auth: Auth = Depends(require(P.SETTINGS_WRITE))):
    """Run the connection test: it asks Claude Code one harmless question to prove it starts, is signed in, and has no tools."""
    if not auth.actor.is_owner:
        raise HTTPException(403, "Only the owner can run the connection test.")
    with _test_lock:
        if _test["state"] == "running" and _test["started_at"] and (utcnow() - _test["started_at"]).total_seconds() < 180:
            return {"state": "running"}
        _test.update(state="running", started_at=utcnow(), report=None)
    threading.Thread(target=_run_test, daemon=True, name="ai-connection-test").start()
    return {"state": "running"}


@router.get("/ai/activity")
def activity(limit: int = 40, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    rows = list(db.scalars(select(AiGeneration).order_by(AiGeneration.id.desc()).limit(max(1, min(limit, 200)))))
    since = finance.day_start()
    by_status = dict(db.execute(select(AiGeneration.status, func.count()).where(AiGeneration.created_at >= since).group_by(AiGeneration.status)).all())
    return {"today": by_status, "calls": [_row(g) for g in rows]}


@router.get("/ai/generations/{gid}")
def generation(gid: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.DIAG_READ))):
    g = db.get(AiGeneration, gid)
    if g is None:
        raise HTTPException(404, "No such call.")
    body: dict[str, Any] | None = None
    if g.artifact_path:
        try:
            body = json.loads(resolve_data_path(g.artifact_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnsafePath):
            body = None
    return {**_row(g), "prompt_sha256": g.prompt_sha256, "output": g.output, "record": body}


@router.post("/ai/ideas")
def ask_for_ideas(body: IdeasIn, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.TASK_CREATE))):
    """Ask the content strategist to propose ideas. They arrive assessed, as proposals; choosing which to make stays with you."""
    st = gateway.brain_state(db)
    if not st["ready"]:
        raise HTTPException(409, f"The AI brain is not ready: {st['detail']}")
    org.get_channel(db, body.channel)
    stamp = utcnow().strftime("%Y%m%d%H%M")
    t = tasks.create(db, org.actor_for_slug(db, "production-manager"), title=f"Propose {body.count} video idea(s) for {body.channel}", task_type="ai.propose_ideas",
                     assigned_agent_slug="content-strategy-specialist", channel_slug=body.channel, input_refs={"count": body.count},
                     idempotency_key=f"ai-ideas:{body.channel}:{stamp}")
    return {"task_id": t.id, "status": t.status}


@router.post("/ideas/{idea_id}/start-with-ai")
def start_with_ai(idea_id: int, db: Session = Depends(get_wdb, scope="function"), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    """Turn an approved idea into a video and let the AI company write, build, check and prepare it. It stops at every decision that is yours."""
    return conductor.start_with_ai(db, auth.actor, idea_id)


@router.get("/videos/{video_id}/progress")
def progress(video_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    from app.models.videos import Video

    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Video not found.")
    return conductor.progress(db, v)
