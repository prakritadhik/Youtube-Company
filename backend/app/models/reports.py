from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class Report(Base):
    """Department, operations or executive report (structured JSON content)."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # app.enums.ReportKind
    title: Mapped[str] = mapped_column(String(200))
    period_start: Mapped[datetime] = mapped_column(UTCDateTime)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    author: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="SUBMITTED")
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    parent_report_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    board_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
