"""Operations records: recurring schedules, alerts and worker heartbeats."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class Schedule(Base):
    """A recurring job. Each due *slot* creates at most one task (the slot is part of the task's idempotency key)."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    cadence: Mapped[dict[str, Any]] = mapped_column(JSON)  # {"kind": "interval", "every_minutes": 60} | {"kind": "daily", "at": "08:30"} | {"kind": "weekly", "days": [0], "at": "09:00"}
    task_type: Mapped[str] = mapped_column(String(60))
    assigned_agent_slug: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # what to do about slots that came due while the app was not running: run the most recent one, or skip them
    catch_up: Mapped[str] = mapped_column(String(10), default="latest")  # latest | skip
    last_slot: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Alert(Base):
    """Something the owner should know about. One open alert per ``key``: repeats raise its count, not a new row."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    severity: Mapped[str] = mapped_column(String(10))  # info | warning | critical
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(60), default="system")
    status: Mapped[str] = mapped_column(String(14), default="OPEN")  # OPEN | ACKNOWLEDGED | RESOLVED
    count: Mapped[int] = mapped_column(Integer, default=1)
    related: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    acknowledged_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class WorkerHeartbeat(Base):
    """Proof that a worker process is alive. A stale beat means scheduled work is not running."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(120), default="")
    pid: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_beat: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    current_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
