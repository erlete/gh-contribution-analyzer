"""Persons, their immutable identities and merge suggestions."""

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gca.db.base import Base, JSONVariant


class IdentityKind(enum.StrEnum):
    GIT_AUTHOR = "git_author"
    GITHUB_LOGIN = "github_login"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    identities: Mapped[list[Identity]] = relationship(back_populates="person")


class Identity(Base):
    """One observed author identity. Immutable: merges only move the person FK."""

    __tablename__ = "identities"
    __table_args__ = (
        Index(
            "ix_identities_git_author_key",
            "name_norm",
            "email_norm",
            unique=True,
            postgresql_where="kind = 'git_author'",
            sqlite_where="kind = 'git_author'",
        ),
        Index(
            "ix_identities_github_login_key",
            "login_norm",
            unique=True,
            postgresql_where="kind = 'github_login'",
            sqlite_where="kind = 'github_login'",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[IdentityKind] = mapped_column(
        Enum(IdentityKind, native_enum=False, length=20)
    )
    name: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(320))
    login: Mapped[str | None] = mapped_column(String(200))
    name_norm: Mapped[str] = mapped_column(String(300), default="")
    email_norm: Mapped[str] = mapped_column(String(320), default="")
    login_norm: Mapped[str] = mapped_column(String(200), default="")
    node_id: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    person: Mapped[Person] = relationship(back_populates="identities")


class MergeSuggestion(Base):
    __tablename__ = "merge_suggestions"
    __table_args__ = (UniqueConstraint("person_a_id", "person_b_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_a_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    person_b_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list | None] = mapped_column(JSONVariant)  # type: ignore[type-arg]
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
