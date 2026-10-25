from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))  # birthday | medicine | story
    objective: Mapped[str] = mapped_column(Text)
    brand: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Hard rules that cannot be relaxed by settings (e.g. medical review is mandatory).
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    youtube_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Agent(Base):
    """A controlled role: responsibilities, allowed tools, schemas, manager and limits."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    level: Mapped[str] = mapped_column(String(16))  # app.enums.AgentLevel
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    reports_to_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    responsibilities: Mapped[str] = mapped_column(Text)
    required_inputs: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_format: Mapped[list[str]] = mapped_column(JSON, default=list)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    extra_permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    execution_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    uses_ai_model: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    department: Mapped[Department | None] = relationship()
    manager: Mapped[Agent | None] = relationship(remote_side="Agent.id")
