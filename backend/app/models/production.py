"""Phase 2 production records: provenance-tracked assets, briefs, scripts, sources, reviews, QA."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class Asset(Base):
    """Any file used in a video, with where it came from and what it may be used for."""

    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_video", "video_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))  # image|music|narration|caption|font|thumbnail|clip|video
    role: Mapped[str] = mapped_column(String(40), default="")  # e.g. scene-1, title-card, final
    path: Mapped[str] = mapped_column(String(500))  # relative to data/, or "resource:<path>" for bundled files
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)

    provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    generation_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    license_type: Mapped[str] = mapped_column(String(40), default="unknown")
    license_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    commercial_use: Mapped[str] = mapped_column(String(10), default="unknown")  # yes | no | unknown
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    simulated: Mapped[bool] = mapped_column(Boolean, default=False)  # a stand-in; can never clear the gate
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CreativeBrief(Base):
    __tablename__ = "creative_briefs"
    __table_args__ = (UniqueConstraint("video_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="DRAFT")  # DRAFT | APPROVED | REJECTED
    created_by: Mapped[str] = mapped_column(String(100))
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    board_version_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    board_digest_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SourceRecord(Base):
    """A source consulted for a video: URL, retrieval date, note and the claim it supports."""

    __tablename__ = "source_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(300), default="")
    publisher: Mapped[str] = mapped_column(String(200), default="")
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime)
    note: Mapped[str] = mapped_column(Text, default="")
    supports: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ScriptVersion(Base):
    """A saved script, lyrics, narration or storyboard. History feeds the anti-repetition checks."""

    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("video_id", "kind", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12))  # script | lyrics | narration | storyboard
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    author_kind: Mapped[str] = mapped_column(String(10))  # human | agent | provider
    author_label: Mapped[str] = mapped_column(String(120))
    # lines of the form {"claim": "...", "source_ids": [1, 2]} (medicine scripts require them)
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    structure: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OriginalityReview(Base):
    __tablename__ = "originality_reviews"
    __table_args__ = (UniqueConstraint("video_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class QAReport(Base):
    __tablename__ = "qa_reports"
    __table_args__ = (UniqueConstraint("video_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
