"""Alembic migrations against a real Postgres (CI service container).

Skipped when TEST_DATABASE_URL is unset so plain local runs stay green.
"""

import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")
def test_migrations_upgrade_downgrade_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    from gca.db.migrate import run_migrations

    run_migrations(TEST_URL)

    engine = sa.create_engine(TEST_URL)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                sa.text(
                    "select table_name from information_schema.tables"
                    " where table_schema = 'public'"
                )
            )
        }
    assert "alembic_version" in tables
    assert {"orgs", "repos", "commits", "person_repo_day_stats"} <= tables

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_URL.replace("%", "%%"))
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        remaining = {
            row[0]
            for row in conn.execute(
                sa.text(
                    "select table_name from information_schema.tables"
                    " where table_schema = 'public'"
                )
            )
        }
    assert "orgs" not in remaining

    run_migrations(TEST_URL)
    engine.dispose()
