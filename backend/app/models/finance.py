from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.enums import CostKind
from app.timeutil import utcnow


class UsageRecord(Base):
    """One provider call or resource use. Costs are integer micro-USD (1e-6 USD) to sum exactly."""

    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_records_ts", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    provider: Mapped[str] = mapped_column(String(60))
    operation: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(20), default="other")  # model|image|audio|video|storage|other
    units: Mapped[float | None] = mapped_column(nullable=True)
    unit_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_kind: Mapped[str] = mapped_column(String(30), default=CostKind.ESTIMATE.value)
    discretionary: Mapped[bool] = mapped_column(Boolean, default=True)  # paid, optional spend
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), nullable=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
