"""Declarative base with a deterministic naming convention."""

from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, MetaData, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB on Postgres, plain JSON elsewhere (sqlite in unit tests).
JSONVariant = JSON().with_variant(JSONB(), "postgresql")

# BIGINT on Postgres; plain INTEGER on sqlite so autoincrement works in tests.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


@event.listens_for(Engine, "connect")
def _sqlite_fk_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    """SQLite ignores ON DELETE CASCADE unless foreign_keys is switched on."""
    if "sqlite" not in type(dbapi_connection).__module__:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
