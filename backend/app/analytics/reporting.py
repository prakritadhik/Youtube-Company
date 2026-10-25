"""Claims the Analytics and Community managers make in their department reports.

Every number is a count the CEO can re-run against the database. What could not be collected is stated as unavailable, with a check
that the source really is not providing it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.enums import ClaimClass
from app.security.permissions import Actor
from app.services import reports


def claims(db: Session, actor: Actor, start: datetime, end: datetime, who: str, dept: str) -> list[reports.Claim]:
    window = {"start": start.isoformat(), "end": end.isoformat()}
    out: list[reports.Claim] = []

    def add(text: str, entity: str, where: dict[str, Any], column: str | None = None, windowed: bool = True) -> None:
        chk: dict[str, Any] = {"type": "count", "entity": entity, "where": where}
        if windowed:
            chk["between"] = {**({"column": column} if column else {}), **window}
        n = reports.run_check(db, {**chk, "expected": -1}, actor).actual
        chk["expected"] = n
        out.append(reports.Claim(text.format(n=n), ClaimClass.VERIFIED_FACT, n, chk, f"{entity} table", who))

    if dept == "analytics-improvement":
        add("{n} channel(s) are connected to YouTube.", "youtube_connections", {"status": "CONNECTED"}, windowed=False)
        add("{n} performance figure(s) were retrieved from YouTube in the period.", "metrics", {})
        add("{n} figure(s) are recorded as unavailable, each with YouTube's reason.", "metric_gaps", {}, windowed=False)
        add("{n} lesson(s) are waiting for investigation, verification or the owner's approval.", "board_entries", {"status": ["OBSERVED", "UNDER_INVESTIGATION", "VERIFIED"]}, windowed=False)
        add("{n} lesson(s) or experiments are in force as active instructions.", "board_entries", {"status": "ACTIVE_INSTRUCTION"}, windowed=False)
        add("{n} experiment(s) are running.", "experiments", {"status": "RUNNING"}, windowed=False)
        add("{n} experiment(s) were concluded in the period.", "experiments", {"status": "CONCLUDED"}, "concluded_at")
    else:
        add("{n} comment(s) were fetched in the period.", "comments", {})
        add("{n} comment(s) in the period were escalated for a person (safety, copyright or medical).", "comments", {"risk": "escalate"})
        add("{n} reply draft(s) were written in the period.", "reply_drafts", {})
        add("{n} reply(ies) were posted and read back from YouTube in the period.", "reply_drafts", {"status": "SENT"}, "sent_at")
        out.append(reports.Claim("A language-model reading of audience sentiment is unavailable: comments are sorted by fixed rules (rules-v1), which are blunt and English-only.", ClaimClass.UNAVAILABLE, None,
                                 {"type": "source_not_connected", "source": "sentiment_model"}, "integration status", who))
    return out
