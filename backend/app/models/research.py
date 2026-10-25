"""Phase 3 records: content ideas, research notes, metadata sets and thumbnail concepts.

Every number here is either computed from other records or carries its own source and date. Nothing is a
forecast: search volume, trend strength and competitor statistics exist only when someone recorded them with a
source, otherwise the field says so.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class ContentIdea(Base):
    """A candidate video concept moving through assessment, approval and (when chosen) production."""

    __tablename__ = "content_ideas"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    title: Mapped[str] = mapped_column(String(200))
    concept: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(300))
    rationale: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(24), default="manual")  # owner | agent | board | manual
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")
    # proposer's judgement, 1-5; kept apart from measured facts and labelled as judgement in the score explanation
    audience_relevance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    est_cost_micro_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)  # an estimate; None means unavailable
    est_production_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requires: Mapped[list[str]] = mapped_column(JSON, default=list)  # capability needs, e.g. sung_vocals
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    assessment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    assessed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    planned_for: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ISO date
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ResearchNote(Base):
    """One piece of recorded research: a source, a trend, a competitor observation or an audience question.

    The text is *untrusted data*. It is cleaned, length-capped, scanned for instruction-like content and always
    quoted as data when shown to an agent. It is never executed or followed.
    """

    __tablename__ = "research_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), nullable=True)
    idea_id: Mapped[int | None] = mapped_column(ForeignKey("content_ideas.id"), nullable=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))  # source | topic_info | trend | competitor | audience_question
    url: Mapped[str] = mapped_column(String(1000), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    publisher: Mapped[str] = mapped_column(String(200), default="")
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime)
    method: Mapped[str] = mapped_column(String(24))  # fetched | owner_provided | manual_entry
    excerpt: Mapped[str] = mapped_column(Text, default="")
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)  # relative to the data dir
    supports: Mapped[str] = mapped_column(Text, default="")  # the claim this note supports
    note: Mapped[str] = mapped_column(Text, default="")
    # numeric claims (views, subscribers, search volume ...) each need a source and a date
    figures: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_class: Mapped[str] = mapped_column(String(16), default="unrated")  # authoritative | reputable | general | unrated
    injection_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    untrusted: Mapped[bool] = mapped_column(Boolean, default=True)
    promoted_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MetadataSet(Base):
    """A versioned set of title/description/tags/hashtags/chapters for one video, with its check results."""

    __tablename__ = "metadata_sets"
    __table_args__ = (UniqueConstraint("video_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")  # DRAFT | CHECKED | NEEDS_REVISION | APPROVED | SUPERSEDED
    title_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    chosen_title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    chapters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    playlists: Mapped[list[str]] = mapped_column(JSON, default=list)
    pinned_comment: Mapped[str] = mapped_column(Text, default="")
    call_to_action: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    generated_by: Mapped[str] = mapped_column(String(60), default="manual")  # template-v1 | manual | llm
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ThumbnailConcept(Base):
    """A thumbnail idea assessed before any image is made."""

    __tablename__ = "thumbnail_concepts"
    __table_args__ = (UniqueConstraint("video_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")  # DRAFT | CHECKED | NEEDS_REVISION | APPROVED | SUPERSEDED
    overlay_text: Mapped[str] = mapped_column(String(80), default="")
    composition: Mapped[str] = mapped_column(Text, default="")
    palette: Mapped[list[str]] = mapped_column(JSON, default=list)
    relation_to_content: Mapped[str] = mapped_column(Text, default="")
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
