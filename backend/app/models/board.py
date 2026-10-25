from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.enums import BoardStatus, Confidence, RiskClass
from app.timeutil import utcnow


class BoardEntry(Base):
    """A lesson, instruction, experiment or finding on the company intelligence board."""

    __tablename__ = "board_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(20), unique=True)  # e.g. LSN-000012
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), nullable=True)  # NULL=company
    status: Mapped[str] = mapped_column(String(24), default=BoardStatus.OBSERVED.value)
    confidence: Mapped[str] = mapped_column(String(10), default=Confidence.LOW.value)
    risk_class: Mapped[str] = mapped_column(String(12), default=RiskClass.ROUTINE.value)
    responsible_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    test_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class BoardEntryVersion(Base):
    __tablename__ = "board_entry_versions"
    __table_args__ = (UniqueConstraint("entry_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("board_entries.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(100))
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class BoardRelease(Base):
    """Global board version. Bumped whenever the set of instructions workers must follow changes."""

    __tablename__ = "board_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    entry_ids: Mapped[list[int]] = mapped_column(JSON, default=list)  # entries that changed
    affected_channel_ids: Mapped[list[int | None]] = mapped_column(JSON, default=list)  # null=company-wide
    digests: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)  # channel slug|"*" -> sha256


class BoardAcknowledgement(Base):
    __tablename__ = "board_acknowledgements"
    __table_args__ = (UniqueConstraint("agent_id", "scope", "digest"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    scope: Mapped[str] = mapped_column(String(40))  # channel slug or "*"
    release_version: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    acknowledged_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
