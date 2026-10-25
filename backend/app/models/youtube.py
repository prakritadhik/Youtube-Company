"""YouTube integration records: the connection to each channel, OAuth state, uploads and API quota use."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class YouTubeConnection(Base):
    """Which YouTube channel an internal channel publishes to, and the (encrypted) permission to do so.

    The identity is *reported by Google* after sign-in and then confirmed by the owner; the app never assumes that the
    Google account that signed in owns the channel it was meant for.
    """

    __tablename__ = "youtube_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING_CONFIRMATION")  # PENDING_CONFIRMATION | CONNECTED | NEEDS_RECONNECT | REVOKED
    google_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    google_channel_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    google_channel_handle: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Both are the owner's declaration, never a guess: YouTube requires an audience setting, and the right answer for a
    # channel of children's songs is a legal judgement that belongs to its owner.
    made_for_kids: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    category_id: Mapped[str] = mapped_column(String(4), default="22")
    confirmed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class YouTubeAuthState(Base):
    """One pending sign-in. Single use, short-lived; only a hash of the state value is kept."""

    __tablename__ = "youtube_auth_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    verifier_enc: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(String(300))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)  # what this sign-in asked Google for
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class YouTubeUpload(Base):
    """One attempt to put one video on YouTube, from the first request to the verified read-back.

    The row is written *before* anything irreversible happens and updated at every step, so a crash at any point can be
    reconciled (resume the same upload session, or look the video up) instead of creating a second copy.
    """

    __tablename__ = "youtube_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)  # none for a connection self-test
    connection_id: Mapped[int] = mapped_column(ForeignKey("youtube_connections.id"))
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # exactly what will be asked of YouTube, frozen when the upload is prepared (title, description, tags, category, audience)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    file_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)  # relative to data/
    thumbnail_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # PREPARED | SESSION_OPEN | UPLOADED | VERIFIED | PRIVACY_LOCKED | MISMATCH | FAILED | ABANDONED
    status: Mapped[str] = mapped_column(String(20), default="PREPARED")
    privacy_requested: Mapped[str] = mapped_column(String(10))
    publish_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, default=0)
    session_uri_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # a capability URL: stored encrypted
    youtube_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)  # saved API response (relative to data/)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    thumbnail_status: Mapped[str] = mapped_column(String(16), default="NONE")  # NONE | SET | FAILED | SKIPPED
    thumbnail_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class QuotaEntry(Base):
    """Every call this app made that costs YouTube API quota. The Google Cloud console remains the authority."""

    __tablename__ = "youtube_quota_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    method: Mapped[str] = mapped_column(String(40))
    units: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    video_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="")
