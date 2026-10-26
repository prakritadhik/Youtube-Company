from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class AiGeneration(Base):
    """One call to the AI brain (Claude Code on this computer): who asked, what for, what came back, what it was recorded as.

    The full prompt and answer are kept in a file (``artifact_path``); the row holds a hash of the prompt so the file can be
    checked, and the parsed result that was actually used. ``list_price_micro_usd`` is the program's own list-price figure
    for the tokens used. It is not a bill: the owner's plan decides what is charged.
    """

    __tablename__ = "ai_generations"
    __table_args__ = (Index("ix_ai_generations_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    purpose: Mapped[str] = mapped_column(String(40))  # brief.draft | script.draft | ideas.propose | review.brief | review.script | ...
    role_slug: Mapped[str] = mapped_column(String(80))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(10))  # OK | INVALID | FAILED
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    prompt_chars: Mapped[int] = mapped_column(Integer)
    response_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    list_price_micro_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
