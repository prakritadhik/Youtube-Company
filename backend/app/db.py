"""Database engine, session handling and shared column types."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import DateTime, TypeDecorator

from app.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime rejected; use timezone-aware UTC datetimes")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _make_engine(url: str, *, immediate: bool = False) -> Engine:
    """Create an engine. For SQLite, ``immediate=True`` makes every transaction ``BEGIN IMMEDIATE``.

    Write sessions use it so concurrent writers (API + worker) queue on the database lock instead of
    failing on a stale read snapshot, which also keeps the audit hash chain linear.
    """
    kwargs: dict[str, Any] = {"future": True}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            if immediate:
                dbapi_conn.isolation_level = None  # we emit BEGIN ourselves
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        if immediate:

            @event.listens_for(engine, "begin")
            def _begin(conn):  # noqa: ANN001
                conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


_engines: dict[bool, Engine] = {}
_makers: dict[bool, sessionmaker[Session]] = {}
_url_override: str | None = None


def _url() -> str:
    return _url_override or get_settings().db_url


def _ensure(write: bool) -> None:
    if write not in _engines:
        url = _url()
        # Non-SQLite databases do not need the separate immediate engine.
        eng = _make_engine(url, immediate=write and url.startswith("sqlite"))
        _engines[write] = eng
        _makers[write] = sessionmaker(bind=eng, expire_on_commit=False, autoflush=False)


def get_engine(write: bool = False) -> Engine:
    _ensure(write)
    return _engines[write]


def configure_engine(url: str) -> None:
    """Point the process at a specific database (tests, restore tooling)."""
    global _url_override
    dispose_engines()
    _url_override = url


def dispose_engines() -> None:
    for e in _engines.values():
        e.dispose()
    _engines.clear()
    _makers.clear()


def new_session(write: bool = True) -> Session:
    _ensure(write)
    return _makers[write]()


@contextmanager
def session_scope(write: bool = True) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    s = new_session(write)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency for read-only requests."""
    s = new_session(write=False)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def get_wdb() -> Iterator[Session]:
    """FastAPI dependency for requests that write (takes the write lock up front)."""
    s = new_session(write=True)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
