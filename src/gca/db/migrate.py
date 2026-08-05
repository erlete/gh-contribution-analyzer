"""Schema migration runner.

Serialized across app and worker containers with a Postgres advisory lock so
concurrent startups cannot race Alembic.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from gca.config import get_settings

_LOCK_KEY = 743_002_001


def run_migrations(database_url: str | None = None) -> None:
    url = database_url or get_settings().database_url
    ini = Path("alembic.ini")
    if not ini.exists():
        ini = Path("/app/alembic.ini")
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    if url.startswith("postgresql"):
        engine = sa.create_engine(url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT pg_advisory_lock(:key)"), {"key": _LOCK_KEY})
            command.upgrade(cfg, "head")
            conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
        engine.dispose()
    else:
        command.upgrade(cfg, "head")


def main() -> None:
    run_migrations()


if __name__ == "__main__":
    main()
