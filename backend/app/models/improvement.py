"""Phase 6 records: experiments (with their video arms) and the link between mined lessons and the videos they came from.

An experiment is a *plan plus its results*; nothing in it is a forecast. Its outcome figures are read from retrieved metrics (or figures the owner
entered with a source), and its analysis states what the numbers can and cannot support.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(16), unique=True)  # EXP-000001
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    title: Mapped[str] = mapped_column(String(200))
    hypothesis: Mapped[str] = mapped_column(Text)
    change: Mapped[str] = mapped_column(Text)  # what is done differently for the treatment videos
    metric: Mapped[dict[str, Any]] = mapped_column(JSON)  # {metric, source, aggregate, window_days, higher_is_better}
    baseline: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # measured when the experiment starts
    min_per_group: Mapped[int] = mapped_column(Integer, default=5)
    confounders: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="DESIGN")  # DESIGN | RUNNING | CONCLUDED | CANCELLED
    seed: Mapped[int] = mapped_column(Integer)
    board_entry_id: Mapped[int | None] = mapped_column(ForeignKey("board_entries.id"), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the experiment this one repeats
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    analysed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(8), nullable=True)  # adopt | revise | repeat | reject
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    concluded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ExperimentVideo(Base):
    """One video's arm. Assigned by a seeded random draw *before* the video is published, and never changed afterwards."""

    __tablename__ = "experiment_videos"
    __table_args__ = (UniqueConstraint("experiment_id", "video_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    arm: Mapped[str] = mapped_column(String(9))  # control | treatment
    assigned_by: Mapped[str] = mapped_column(String(100))
    assigned_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    draw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # how the arm was chosen, so it can be re-checked


class WorkflowLesson(Base):
    """Which videos have been examined for lessons, and which lesson (if any) each finding became. Prevents duplicates."""

    __tablename__ = "workflow_lessons"
    __table_args__ = (UniqueConstraint("video_id", "kind", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    kind: Mapped[str] = mapped_column(String(12))  # qa | originality | examined
    key: Mapped[str] = mapped_column(String(120))
    entry_id: Mapped[int | None] = mapped_column(ForeignKey("board_entries.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
