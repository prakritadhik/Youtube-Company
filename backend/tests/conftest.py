from __future__ import annotations

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


@pytest.fixture
def env(tmp_path, monkeypatch) -> Iterator[Path]:
    monkeypatch.setenv("YTC_DATA_DIR", str(tmp_path / "data"))
    url = f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}"
    monkeypatch.setenv("YTC_DATABASE_URL", url)
    monkeypatch.setenv("YTC_TASK_BACKOFF_BASE_SECONDS", "10")
    reset_settings_cache()
    dispose_engines()
    configure_engine(url)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")  # the real migration builds the test database
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
