"""Lessons, experiments and comparisons. Reading the working view of lessons is owner-only (raw findings live in the diagnostic layer); every change to a
lesson's status or an experiment's fate is the owner's, and the board's own rules still apply underneath."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Auth, require
from app.db import get_db, get_wdb
from app.improvement import board_actions, compare, experiments
from app.models.board import BoardEntry
from app.models.improvement import Experiment
from app.models.videos import Video
from app.security.permissions import P
from app.services import audit, tasks
from app.timeutil import utcnow

router = APIRouter(prefix="/api", tags=["improvement"])


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=1000)


class ExperimentIn(BaseModel):
    channel: str = Field(max_length=60)
    title: str = Field(min_length=3, max_length=200)
    hypothesis: str = Field(min_length=10, max_length=2000)
    change: str = Field(min_length=5, max_length=2000)
    metric: dict[str, Any]
    min_per_group: int = 5
    confounders: list[str] = Field(default_factory=list, max_length=10)


class EnrollIn(BaseModel):
    video_id: int


class DecideIn(BaseModel):
    decision: str = Field(max_length=10)
    note: str = Field(min_length=1, max_length=2000)


@router.get("/improvement/overview")
def overview(db: Session = Depends(get_db), auth: Auth = Depends(require(P.DIAG_READ))):
    by_status = dict(db.execute(select(BoardEntry.status, func.count()).group_by(BoardEntry.status)).all())
    exp_status = dict(db.execute(select(Experiment.status, func.count()).group_by(Experiment.status)).all())
    from app.improvement import lessons as lessons_svc

    return {"lessons_by_status": by_status, "experiments_by_status": exp_status, "unexamined_workflows": len(lessons_svc.candidates(db, 500)),
            "lessons": board_actions.lessons(db), "experiments": experiments.list_all(db),
            "notes": ["A lesson becomes an instruction only after investigation, the CEO's verification against its evidence, and your approval.",
                      "Lessons are specific to one channel; one channel's lesson is never sent to another channel's workers."]}


@router.post("/improvement/mine")
def mine_now(db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    """Queue the lesson-mining task now instead of waiting for the daily cycle. Reads recorded history only."""
    t = tasks.create(db, auth.actor, title="Look for lessons in completed workflows", task_type="improvement.mine_lessons", assigned_agent_slug="continuous-improvement-scientist",
                     idempotency_key=f"mine-lessons:manual:{utcnow():%Y%m%d%H%M}")
    audit.record(db, auth.actor, "improvement.mine_requested", "task", t.id, {})
    return {"task_id": t.id, "status": t.status}


@router.post("/board/{public_id}/{action}")
def board_action(public_id: str, action: str, body: ReasonIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.BOARD_ACTIVATE))):
    return board_actions.act(db, auth.actor, public_id, action, body.reason)


@router.get("/experiments")
def list_experiments(db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    return {"experiments": experiments.list_all(db)}


@router.post("/experiments")
def create_experiment(body: ExperimentIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.BOARD_PROPOSE))):
    return experiments.serialize(db, experiments.create(db, auth.actor, channel_slug=body.channel, title=body.title, hypothesis=body.hypothesis, change=body.change,
                                                         metric=body.metric, min_per_group=body.min_per_group, confounders=body.confounders), detail=True)


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    e = db.get(Experiment, exp_id)
    if e is None:
        raise HTTPException(404, "Experiment not found.")
    return experiments.serialize(db, e, detail=True)


@router.post("/experiments/{exp_id}/enroll")
def enroll(exp_id: int, body: EnrollIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.VIDEO_MANAGE))):
    row = experiments.enroll(db, auth.actor, exp_id, body.video_id)
    return {"video_id": row.video_id, "arm": row.arm, "draw": row.draw}


@router.post("/experiments/{exp_id}/start")
def start(exp_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.BOARD_ACTIVATE))):
    return experiments.serialize(db, experiments.start(db, auth.actor, exp_id), detail=True)


@router.post("/experiments/{exp_id}/analyse")
def analyse(exp_id: int, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.BOARD_PROPOSE))):
    experiments.analyse(db, auth.actor, exp_id)
    return experiments.serialize(db, experiments.get(db, exp_id), detail=True)


@router.post("/experiments/{exp_id}/decide")
def decide(exp_id: int, body: DecideIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.BOARD_ACTIVATE))):
    return experiments.decide(db, auth.actor, exp_id, body.decision, body.note)


@router.post("/experiments/{exp_id}/cancel")
def cancel(exp_id: int, body: ReasonIn, db: Session = Depends(get_wdb), auth: Auth = Depends(require(P.BOARD_ACTIVATE))):
    return experiments.serialize(db, experiments.cancel(db, auth.actor, exp_id, body.reason), detail=True)


@router.get("/videos/{video_id}/experiments")
def video_experiments(video_id: int, db: Session = Depends(get_db), auth: Auth = Depends(require(P.EXEC_READ))):
    if db.get(Video, video_id) is None:
        raise HTTPException(404, "Video not found.")
    return {"experiments": experiments.for_video(db, video_id)}


@router.get("/compare")
def compare_videos(channel: str, metric: str = "views", aggregate: str | None = None, window_days: int = 7, db: Session = Depends(get_db),
                   auth: Auth = Depends(require(P.EXEC_READ))):
    spec: dict[str, Any] = {"metric": metric, "window_days": window_days}
    if aggregate:
        spec["aggregate"] = aggregate
    return compare.compare(db, channel, spec)
