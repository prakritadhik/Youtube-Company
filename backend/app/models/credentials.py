from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class Credential(Base):
    """A key or client setting the owner pasted into the dashboard. The value is encrypted with the same key file as the YouTube tokens."""

    __tablename__ = "credentials"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
    hint: Mapped[str] = mapped_column(String(12), default="")  # the last four characters of a secret, so the owner can tell which one is saved
    updated_by: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
