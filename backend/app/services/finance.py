"""Usage records, spend summaries and the budget guard.

Costs are stored as integer micro-USD. ``actual`` charges, ``estimate`` figures and
``included_in_subscription`` usage are kept apart and never silently added together.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import CostKind
from app.models.finance import UsageRecord
from app.models.org import Channel
from app.security.permissions import Actor, P
from app.services import audit, control
from app.services import settings as settings_svc
from app.timeutil import utcnow

MICRO = 1_000_000


class BudgetExceeded(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def usd_to_micro(usd: float) -> int:
    return int(round(usd * MICRO))


def micro_to_usd(micro: int | None) -> float | None:
    return None if micro is None else micro / MICRO


def day_start(now: datetime | None = None) -> datetime:
    n = now or utcnow()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(now: datetime | None = None) -> datetime:
    return day_start(now).replace(day=1)


def record_usage(
    db: Session,
    actor: Actor,
    *,
    provider: str,
    operation: str,
    cost_micro_usd: int = 0,
    cost_kind: CostKind = CostKind.ESTIMATE,
    category: str = "other",
    units: float | None = None,
    unit_type: str | None = None,
    discretionary: bool = True,
    channel_slug: str | None = None,
    video_id: int | None = None,
    task_id: int | None = None,
    meta: dict[str, Any] | None = None,
) -> UsageRecord:
    actor.require(P.FINANCE_RECORD)
    if cost_micro_usd < 0:
        raise ValueError("cost cannot be negative")
    channel_id = None
    if channel_slug:
        ch = db.scalar(select(Channel).where(Channel.slug == channel_slug))
        channel_id = ch.id if ch else None
    rec = UsageRecord(
        provider=provider, operation=operation, category=category, units=units, unit_type=unit_type,
        cost_micro_usd=cost_micro_usd, cost_kind=cost_kind.value, discretionary=discretionary,
        channel_id=channel_id, video_id=video_id, task_id=task_id, meta=meta or {},
    )
    db.add(rec)
    db.flush()
    return rec


def _spent(db: Session, since: datetime, *, video_id: int | None = None) -> int:
    """Budget-consuming spend since ``since``: actual charges and estimates, not subscription-included."""
    q = select(func.coalesce(func.sum(UsageRecord.cost_micro_usd), 0)).where(
        UsageRecord.ts >= since,
        UsageRecord.discretionary.is_(True),
        UsageRecord.cost_kind.in_([CostKind.ACTUAL.value, CostKind.ESTIMATE.value]),
    )
    if video_id is not None:
        q = q.where(UsageRecord.video_id == video_id)
    return int(db.scalar(q) or 0)


def _count(db: Session, since: datetime, category: str | None) -> int:
    q = select(func.count()).select_from(UsageRecord).where(UsageRecord.ts >= since, UsageRecord.discretionary.is_(True))
    if category:
        q = q.where(UsageRecord.category == category)
    return int(db.scalar(q) or 0)


def authorize(
    db: Session,
    *,
    estimated_micro_usd: int = 0,
    category: str = "other",
    video_id: int | None = None,
    provider_requests: int = 1,
) -> None:
    """Raise unless a discretionary paid operation fits every owner-configured limit.

    Limits of zero mean "not permitted". Retries call this again, so a retry loop cannot spend past
    the limits that applied when the first attempt was made.
    """
    control.assert_external_action_allowed(db, "paid_generation")
    b = settings_svc.get(db, "budget")
    now = utcnow()

    if estimated_micro_usd > 0:
        daily = usd_to_micro(b["daily_usd"])
        if _spent(db, day_start(now)) + estimated_micro_usd > daily:
            raise BudgetExceeded("DAILY_BUDGET", "This operation would exceed the daily spending limit.")
        monthly = usd_to_micro(b["monthly_usd"])
        if _spent(db, month_start(now)) + estimated_micro_usd > monthly:
            raise BudgetExceeded("MONTHLY_BUDGET", "This operation would exceed the monthly spending limit.")
        if video_id is not None:
            per_video = usd_to_micro(b["per_video_usd"])
            if _spent(db, datetime(2000, 1, 1, tzinfo=now.tzinfo), video_id=video_id) + estimated_micro_usd > per_video:
                raise BudgetExceeded("PER_VIDEO_BUDGET", "This operation would exceed the per-video cost limit.")

    since = day_start(now)
    caps = {"model": "daily_model_requests", "image": "daily_image_generations", "audio": "daily_audio_generations"}
    if category in caps and _count(db, since, category) + 1 > b[caps[category]]:
        raise BudgetExceeded("CATEGORY_LIMIT", f"Daily {category} generation limit reached.")
    if _count(db, since, None) + provider_requests > b["daily_provider_requests"]:
        raise BudgetExceeded("REQUEST_LIMIT", "Daily provider request limit reached.")


def spend_summary(db: Session) -> dict[str, Any]:
    now = utcnow()
    out: dict[str, Any] = {"as_of": now.isoformat(), "limits": settings_svc.get(db, "budget")}
    for label, since in (("today", day_start(now)), ("month", month_start(now))):
        by_kind = {
            row[0]: int(row[1])
            for row in db.execute(
                select(UsageRecord.cost_kind, func.coalesce(func.sum(UsageRecord.cost_micro_usd), 0))
                .where(UsageRecord.ts >= since)
                .group_by(UsageRecord.cost_kind)
            )
        }
        n = int(db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.ts >= since)) or 0)
        out[label] = {
            "records": n,
            "actual_usd": (by_kind.get(CostKind.ACTUAL.value, 0)) / MICRO,
            "estimated_usd": (by_kind.get(CostKind.ESTIMATE.value, 0)) / MICRO,
            "included_in_subscription_units": int(
                db.scalar(
                    select(func.count()).select_from(UsageRecord).where(
                        UsageRecord.ts >= since, UsageRecord.cost_kind == CostKind.INCLUDED.value
                    )
                ) or 0
            ),
        }
    out["note"] = (
        "Actual charges and estimates are shown separately. Usage included in a subscription is counted, not priced. "
        "Revenue is not tracked here and is reported as unavailable until an authorised source is connected."
    )
    return out


def retention_cutoff(days: int) -> datetime:
    return utcnow() - timedelta(days=days)


def _audit_budget_block(db: Session, actor: Actor, err: BudgetExceeded, context: dict[str, Any]) -> None:
    audit.record(db, actor, "budget.blocked", None, None, {"code": err.code, **context})
