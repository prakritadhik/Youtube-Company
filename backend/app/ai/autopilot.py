"""One pass of the company working by itself: resume parked work, review what was submitted, move videos forward.

Called from the worker's housekeeping tick. It never raises; each part reports what it did, and a part that fails is logged and the
others still run. It does nothing for work the owner did not start with the AI company.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai import conductor, review
from app.db import session_scope
from app.ops import health

log = logging.getLogger("ai.autopilot")


def step(worker_id: str) -> dict[str, Any]:
    def beat() -> None:
        with session_scope() as db:
            health.beat(db, worker_id)

    out: dict[str, Any] = {"resumed": [], "reviews": [], "conducted": []}
    try:
        with session_scope() as db:
            out["resumed"] = conductor.resume_parked(db)
    except Exception:  # noqa: BLE001
        log.exception("resuming parked AI work failed")
    try:
        out["reviews"] = review.sweep(beat=beat)
    except Exception:  # noqa: BLE001
        log.exception("manager review pass failed")
    try:
        out["conducted"] = conductor.conduct_all()
    except Exception:  # noqa: BLE001
        log.exception("conductor pass failed")
    return out
