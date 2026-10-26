from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.agents.seed import seed_organization
from app.config import reset_settings_cache
from app.db import configure_engine, dispose_engines, new_session
from app.enums import Role
from app.models.auth import User
from app.security.passwords import hash_password
from app.security.permissions import Actor
from app.services import (
    org,
    registry,  # noqa: F401  (registers approval + task handlers)
)

BACKEND = Path(__file__).resolve().parents[1]
FAST_HASH = dict(n=2**10)  # cheap scrypt so tests stay fast; production uses 2**15


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory) -> Path:
    """A database built once per test process by the REAL migrations; each test starts from its own copy of it.

    Running every migration for every test was most of the suite's time (and much more on Windows disks). A copy is byte-for-byte what the
    migrations produce, so the tests still run against the real schema, and ``test_migrations_docs`` still checks the migrations themselves.
    """
    folder = tmp_path_factory.mktemp("template")
    target = folder / "template.sqlite3"
    url = f"sqlite:///{target.as_posix()}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("YTC_DATA_DIR", str(folder / "data"))
        mp.setenv("YTC_DATABASE_URL", url)
        reset_settings_cache()
        dispose_engines()
        command.upgrade(cfg, "head")
        dispose_engines()
    reset_settings_cache()
    for leftover in (target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
        assert not leftover.exists(), "the template database must be fully written before it is copied"
    return target


@pytest.fixture
def env(tmp_path, monkeypatch, migrated_template) -> Iterator[Path]:
    monkeypatch.setenv("YTC_DATA_DIR", str(tmp_path / "data"))
    url = f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}"
    monkeypatch.setenv("YTC_DATABASE_URL", url)
    monkeypatch.setenv("YTC_TASK_BACKOFF_BASE_SECONDS", "10")
    reset_settings_cache()
    dispose_engines()
    shutil.copyfile(migrated_template, tmp_path / "test.sqlite3")
    configure_engine(url)
    yield tmp_path
    dispose_engines()
    reset_settings_cache()


@pytest.fixture
def db(env) -> Iterator[Session]:
    s = new_session()
    seed_organization(s)
    s.commit()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def owner(db) -> Actor:
    u = User(username="owner", password_hash=hash_password("Correct-Horse-9", **FAST_HASH), role=Role.OWNER.value)
    db.add(u)
    db.commit()
    return Actor.for_user(u.id, u.username, u.role)


@pytest.fixture
def viewer(db) -> Actor:
    u = User(username="viewer", password_hash=hash_password("Correct-Horse-9", **FAST_HASH), role=Role.VIEWER.value)
    db.add(u)
    db.commit()
    return Actor.for_user(u.id, u.username, u.role)


@pytest.fixture
def system() -> Actor:
    return Actor.system("test")


@pytest.fixture
def agent(db):
    def _agent(slug: str) -> Actor:
        return org.actor_for_slug(db, slug)

    return _agent


@pytest.fixture
def ceo(agent) -> Actor:
    return agent("ceo")


@pytest.fixture
def coo(agent) -> Actor:
    return agent("coo")
