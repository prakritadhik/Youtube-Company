"""Task handlers available to the queue runner. Phase 1 contains system maintenance handlers only."""

from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy import text

from app.db import session_scope
from app.services import audit, backup, reports
from app.storage import data_root, write_json_artifact
from app.timeutil import iso, utcnow
from app.worker.runner import HandlerResult, TaskContext, handler


@handler("system.noop")
def noop(ctx: TaskContext) -> HandlerResult:
    ctx.check_paused()
    rel = write_json_artifact("system", f"noop-{ctx.task_id}.json", {"task_id": ctx.task_id, "ran_at": iso(utcnow())})
    return HandlerResult(result={"message": "noop completed"}, evidence=[{"kind": "file", "ref": rel, "note": "noop marker"}])


@handler("system.health_check")
def health_check(ctx: TaskContext) -> HandlerResult:
    with session_scope(write=False) as db:
        db.execute(text("SELECT 1"))
        chain = audit.verify_chain(db)
    disk = shutil.disk_usage(data_root())
    report: dict[str, Any] = {
        "checked_at": iso(utcnow()), "database_ok": True, "audit_chain": chain,
        "disk_free_gib": round(disk.free / 1024**3, 1), "ffmpeg": shutil.which("ffmpeg") is not None,
    }
    report["passed"] = bool(chain["ok"] and report["disk_free_gib"] > 1)
    rel = write_json_artifact("system", f"health-{ctx.task_id}.json", report)
    return HandlerResult(result=report, evidence=[{"kind": "test_result", "ref": rel, "note": "health report"}])


@handler("system.backup_database")
def backup_database(ctx: TaskContext) -> HandlerResult:
    info = backup.create_backup(label="scheduled")
    from pathlib import Path

    from app.storage import relative_to_data

    rel = relative_to_data(Path(info["path"]))
    return HandlerResult(result={"backup": info["path"], "bytes": info["bytes"], "integrity": info["integrity"]},
                         evidence=[{"kind": "file", "ref": rel, "note": "verified SQLite backup"}])


@handler("system.generate_executive_report")
def generate_executive_report(ctx: TaskContext) -> HandlerResult:
    with session_scope() as db:
        rep = reports.run_reporting_cycle(db)
        rid = rep.id
    return HandlerResult(result={"executive_report_id": rid},
                         evidence=[{"kind": "db_record", "ref": f"reports:{rid}", "note": "verified executive report"}])
