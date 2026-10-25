"""Local accounting of YouTube API quota.

YouTube resets each project's daily quota at midnight Pacific time. This ledger counts only the calls *this app* made, at
the costs configured in settings (``youtube.costs``); the build environment could not confirm the current figures (see
docs/PROVIDERS.md), so they are configuration rather than constants. Other tools using the same Google Cloud project, or a
different real cost, are invisible here: the Google Cloud console is the authority and the dashboard says so.

An upload is refused *before* it starts when the ledger says it would not fit, and quota is recorded even for failed calls
because YouTube charges most of those too.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.youtube import QuotaEntry
from app.services import settings as settings_svc
from app.timeutil import iso, utcnow

PACIFIC = ZoneInfo("America/Los_Angeles")


class QuotaExceeded(Exception):
    code = "YOUTUBE_QUOTA"

    def __init__(self, message: str):
        super().__init__(message)
        self.reason = message


def day_start(now: datetime | None = None) -> datetime:
    """Start of the current quota day (midnight Pacific), as UTC."""
    now = now or utcnow()
    local = now.astimezone(PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(now.tzinfo)


def used_today(db: Session, now: datetime | None = None) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(QuotaEntry.units), 0)).where(QuotaEntry.ts >= day_start(now))) or 0)


def cost(db: Session, method: str) -> int:
    costs = settings_svc.get(db, "youtube")["costs"]
    if method not in costs:
        raise KeyError(f"no configured quota cost for '{method}'")
    return int(costs[method])


def require(db: Session, *methods: str) -> None:
    """Raise ``QuotaExceeded`` unless every listed call fits in what is left of today's configured budget."""
    cfg = settings_svc.get(db, "youtube")
    need = sum(cost(db, m) for m in methods)
    used = used_today(db)
    if used + need > cfg["daily_quota_units"]:
        raise QuotaExceeded(
            f"This needs about {need} quota units but only {max(0, cfg['daily_quota_units'] - used)} of today's {cfg['daily_quota_units']} remain "
            f"(this app's own count). The quota resets at midnight Pacific time, {iso(day_start() + timedelta(days=1))}.")


def record(db: Session, method: str, *, ok: bool = True, video_id: int | None = None, task_id: int | None = None, note: str = "") -> QuotaEntry:
    e = QuotaEntry(method=method, units=cost(db, method), ok=ok, video_id=video_id, task_id=task_id, note=note[:200])
    db.add(e)
    db.flush()
    return e


def summary(db: Session) -> dict:
    cfg = settings_svc.get(db, "youtube")
    used = used_today(db)
    return {
        "used_today": used, "daily_limit": cfg["daily_quota_units"], "remaining": max(0, cfg["daily_quota_units"] - used),
        "resets_at": iso(day_start() + timedelta(days=1)), "costs": cfg["costs"],
        "basis": "Counts only the calls this app made, at the costs set in Settings. Other tools in the same Google project, or a real cost that "
                 "differs from these figures, are not visible here; the Google Cloud console is the authority.",
    }
