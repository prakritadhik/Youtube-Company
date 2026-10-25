"""Fail fast when the database schema is not at the latest migration."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.db import get_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]


class DatabaseOutdated(RuntimeError):
    pass


def assert_db_current() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with get_engine().connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current != head:
        raise DatabaseOutdated(
            f"The database is at revision {current!r} but the application needs {head!r}. "
            "Run: python -m app.cli migrate"
        )
