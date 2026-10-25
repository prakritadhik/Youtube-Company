"""Phase 5 records: retrieved performance metrics, audience comments and reply drafts.

Everything here is *retrieved or recorded with a source*. A metric that could not be retrieved has no row (and an
``MetricGap`` row says why), so "unavailable" is never confused with zero.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class Metric(Base):
    """One measured number for one day, one video (or channel), from one named source."""

    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("scope", "channel_id", "video_id", "metric", "day", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(8))  # video | channel
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    metric: Mapped[str] = mapped_column(String(48))  # YouTube's own metric names, e.g. views, estimatedMinutesWatched
    day: Mapped[date] = mapped_column(Date)  # the day the number applies to (Pacific, as YouTube reports it)
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24))  # youtube_analytics | youtube_data | owner_entered
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    response_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)  # the saved API response (relative to data/)


class MetricGap(Base):
    """A metric we tried to get and could not, with Google's reason. Shown as 'unavailable: <reason>', never as zero."""

    __tablename__ = "metric_gaps"
    __table_args__ = (UniqueConstraint("channel_id", "video_id", "metric", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    metric: Mapped[str] = mapped_column(String(48))
    source: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
    last_attempt: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=1)


class RetentionPoint(Base):
    """Audience retention at a fraction of the video's length, as YouTube reports it."""

    __tablename__ = "retention_points"
    __table_args__ = (UniqueConstraint("video_id", "ratio"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    ratio: Mapped[float] = mapped_column(Float)  # 0.01 .. 1.00 of the way through the video
    audience_watch_ratio: Mapped[float] = mapped_column(Float)
    relative_retention: Mapped[float | None] = mapped_column(Float, nullable=True)  # versus similar videos, when YouTube gives it
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Comment(Base):
    """A public comment. Its text and author are *untrusted data*: cleaned, scanned, shown quoted, never followed."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_comment_id: Mapped[str] = mapped_column(String(80), unique=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    parent_youtube_id: Mapped[str | None] = mapped_column(String(80), nullable=True)  # set for replies
    author: Mapped[str] = mapped_column(String(200), default="")
    author_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_ours: Mapped[bool] = mapped_column(Boolean, default=False)  # written by the connected channel itself
    text: Mapped[str] = mapped_column(Text)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime)
    category: Mapped[str] = mapped_column(String(24), default="uncategorised")
    category_method: Mapped[str] = mapped_column(String(24), default="")
    risk: Mapped[str] = mapped_column(String(12), default="low")  # low | review | escalate
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    injection_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="NEW")  # NEW | TRIAGED | DRAFTED | REPLIED | IGNORED | ESCALATED
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ReplyDraft(Base):
    """A proposed reply. Nothing is posted until it is approved, and then only through a verified, idempotent task."""

    __tablename__ = "reply_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"))
    text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64), default="")  # what was approved; the sender refuses text that no longer matches
    method: Mapped[str] = mapped_column(String(24), default="template-v1")
    status: Mapped[str] = mapped_column(String(14), default="DRAFT")  # DRAFT | APPROVED | REJECTED | SENDING | SENT | FAILED
    risk: Mapped[str] = mapped_column(String(12), default="review")
    approval_request_id: Mapped[int | None] = mapped_column(ForeignKey("approval_requests.id"), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    youtube_reply_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    response_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
