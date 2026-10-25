from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime
from app.enums import TaskStatus
from app.timeutil import utcnow


class Task(Base):
    """Unit of work and persistent job in one record.

    A worker submits a task; the *manager* verifies evidence before it becomes COMPLETED.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_status_next_run", "status", "next_run_at"),
        Index("ix_tasks_channel", "channel_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    task_type: Mapped[str] = mapped_column(String(80), default="manual")
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.CREATED.value)
    priority: Mapped[int] = mapped_column(Integer, default=50)  # lower number runs first

    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), nullable=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    assigned_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="system")

    input_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=True
    )

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)

    board_version_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    board_digest_used: Mapped[str | None] = mapped_column(String(64), nullable=True)

    estimated_cost_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    actual_cost_micro_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    assigned_agent: Mapped[Any] = relationship("Agent", foreign_keys=[assigned_agent_id])
    evidence: Mapped[list[Evidence]] = relationship(back_populates="task", cascade="all, delete-orphan")


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)


class Evidence(Base):
    """Proof attached to a task or board entry. ``NOTE`` evidence is never sufficient alone."""

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    board_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("board_entries.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # app.enums.EvidenceKind
    ref: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retrieved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    verification_status: Mapped[str] = mapped_column(String(20), default="unchecked")
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    verification_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped[Task | None] = relationship(back_populates="evidence")
