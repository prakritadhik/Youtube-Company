from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.enums import OriginalityStatus, VideoStage
from app.timeutil import utcnow


class Video(Base):
    """A video as it moves through the pipeline. Phase 2 adds artifacts, assets and reviews."""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    working_title: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    stage: Mapped[str] = mapped_column(String(20), default=VideoStage.IDEA.value)
    stage_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    originality_status: Mapped[str] = mapped_column(
        String(24), default=OriginalityStatus.NOT_REVIEWED.value
    )
    originality_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # CEO's independent inspection of the originality evidence (separate from the reviewer's verdict)
    ceo_rights_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    asset_license_issues: Mapped[int] = mapped_column(Integer, default=0)  # count of unlicensed assets
    ceo_rights_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    qa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Evidence chain: the (manager-verified) tasks that produced the verdicts. Plain integers rather
    # than foreign keys because tasks already reference videos.
    originality_review_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qa_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    requires_medical_review: Mapped[bool] = mapped_column(Boolean, default=False)
    medical_review_status: Mapped[str] = mapped_column(String(20), default="NOT_REQUIRED")
    owner_approval_status: Mapped[str] = mapped_column(String(20), default="NOT_REQUESTED")
    gate_exception_approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=True
    )

    final_video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    board_version_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publish_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
