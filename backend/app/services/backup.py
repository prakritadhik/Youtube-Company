"""SQLite backup and restore using the online backup API, with integrity verification."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import _url, dispose_engines
from app.timeutil import iso, utcnow


class BackupError(Exception):
    pass


def sqlite_path() -> Path:
    url = _url()
    if not url.startswith("sqlite:///"):
        raise BackupError("automatic backup supports SQLite only; use your database's own dump tool (pg_dump).")
    return Path(url[len("sqlite:///"):])


def verify_backup(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BackupError(f"backup not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            rows = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return {"path": str(path), "integrity": f"unreadable: {e}", "ok": False, "tables": 0, "rows": {},
                "bytes": path.stat().st_size}
    return {"path": str(path), "integrity": integrity, "ok": integrity == "ok" and "audit_events" in tables,
            "tables": len(tables), "rows": rows, "bytes": path.stat().st_size}


def create_backup(label: str = "manual") -> dict[str, Any]:
    src = sqlite_path()
    if not src.is_file():
        raise BackupError("database file does not exist yet")
    s = get_settings()
    s.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    dest = s.backups_dir / f"company-{stamp}-{label}.sqlite3"
    source = sqlite3.connect(src.as_posix())
    target = sqlite3.connect(dest.as_posix())
    try:
        source.backup(target)  # consistent snapshot even while the app is writing (WAL)
    finally:
        target.close()
        source.close()
    info = verify_backup(dest)
    if not info["ok"]:
        dest.unlink(missing_ok=True)
        raise BackupError(f"backup failed verification: {info['integrity']}")
    _prune(s.backups_dir, s.backup_retention_count)
    return {**info, "created_at": iso(utcnow()), "label": label}


def list_backups() -> list[dict[str, Any]]:
    d = get_settings().backups_dir
    if not d.exists():
        return []
    return [{"path": str(p), "name": p.name, "bytes": p.stat().st_size} for p in sorted(d.glob("company-*.sqlite3"), reverse=True)]


def _prune(directory: Path, keep: int) -> None:
    files = sorted(directory.glob("company-*.sqlite3"))
    for old in files[: max(len(files) - keep, 0)]:
        old.unlink(missing_ok=True)


def restore_backup(backup: Path) -> dict[str, Any]:
    """Replace the live database with a verified backup. Stop the API and worker first."""
    info = verify_backup(backup)
    if not info["ok"]:
        raise BackupError(f"refusing to restore: backup failed verification ({info['integrity']})")
    dest = sqlite_path()
    dispose_engines()
    safety = None
    if dest.is_file():
        safety = dest.with_name(dest.stem + f".pre-restore-{utcnow().strftime('%Y%m%dT%H%M%S')}.sqlite3")
        shutil.copy2(dest, safety)
    for ext in ("-wal", "-shm"):
        Path(str(dest) + ext).unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dest.as_posix())
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return {"restored_from": str(backup), "safety_copy": str(safety) if safety else None, "verified": verify_backup(dest)["ok"]}
