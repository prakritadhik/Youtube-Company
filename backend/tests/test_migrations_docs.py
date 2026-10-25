from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import text

from app import migrations_check
from app.agents.catalog import ROLES
from app.agents.docs import render_hierarchy_markdown
from app.db import get_engine
from tests.conftest import BACKEND


def test_api_refuses_to_start_on_an_outdated_database(env):
    migrations_check.assert_db_current()  # fixture migrated to head
    with get_engine(write=True).begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = '0000'"))
    with pytest.raises(migrations_check.DatabaseOutdated, match="python -m app.cli migrate"):
        migrations_check.assert_db_current()


def test_migrations_match_the_models_and_downgrade_cleanly(env):
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url.render_as_string(hide_password=False)))
    command.check(cfg)  # raises if the models and the migration have drifted apart
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_generated_hierarchy_document_covers_every_role_and_the_permission_model():
    md = render_hierarchy_markdown()
    for r in ROLES:
        assert f"### {r.name}" in md
    assert "Owner-only (never grantable to any agent)" in md and "approval:decide" in md
    assert "human_medical_review_required" in md
