"""Claims the Research and Growth Manager makes in the department report.

Every number is a machine-checkable count that the CEO re-runs against the database. Data the company does not have
(search volume, trend strength, competitor statistics) is covered by one "unavailable" claim, so the owner sees one statement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.enums import ClaimClass
from app.security.permissions import Actor
from app.services import reports

DEPARTMENT = "research-growth"


def claims(db: Session, actor: Actor, start: datetime, end: datetime, who: str) -> list[reports.Claim]:
    window = {"start": start.isoformat(), "end": end.isoformat()}

    def count(entity: str, where: dict[str, Any], column: str = "created_at", windowed: bool = True) -> tuple[int, dict[str, Any]]:
        chk: dict[str, Any] = {"type": "count", "entity": entity, "where": where}
        if windowed:
            chk["between"] = {"column": column, **window}
        n = reports.run_check(db, {**chk, "expected": -1}, actor).actual
        chk["expected"] = n
        return n, chk

    out: list[reports.Claim] = []

    def add(text: str, entity: str, where: dict[str, Any], column: str = "created_at", windowed: bool = True) -> None:
        n, chk = count(entity, where, column, windowed)
        out.append(reports.Claim(text.format(n=n), ClaimClass.VERIFIED_FACT, n, chk, f"{entity} table", who))

    add("{n} content idea(s) were proposed in the period.", "content_ideas", {})
    add("{n} content idea(s) were approved in the period.", "content_ideas", {"status": ["APPROVED", "IN_PRODUCTION"]}, "decided_at")
    add("{n} content idea(s) were rejected in the period.", "content_ideas", {"status": "REJECTED"}, "decided_at")
    add("{n} approved idea(s) are parked, waiting for a capability that does not exist yet.", "content_ideas", {"status": "PARKED"}, windowed=False)
    add("{n} research note(s) were recorded in the period.", "research_notes", {})
    add("{n} research note(s) were recorded as trend observations in the period.", "research_notes", {"kind": "trend"})
    add("{n} metadata set(s) were approved in the period.", "metadata_sets", {"status": "APPROVED"}, "approved_at")
    add("{n} metadata set(s) currently need revision.", "metadata_sets", {"status": "NEEDS_REVISION"}, windowed=False)
    add("{n} thumbnail concept(s) were approved in the period.", "thumbnail_concepts", {"status": "APPROVED"}, "approved_at")
    out.append(reports.Claim("Search volume, keyword demand and trend strength are unavailable: no keyword or trend data source is connected.", ClaimClass.UNAVAILABLE, None,
                             {"type": "source_not_connected", "source": "keyword_data"}, "integration status", who))
    return out
