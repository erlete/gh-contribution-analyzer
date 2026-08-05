"""Organizations, credentials and per-org filters."""

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gca.db.base import Base, JSONVariant


class FilterMode(enum.StrEnum):
    ALL = "all"
    WHITELIST = "whitelist"
    BLACKLIST = "blacklist"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(300), default="")
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    repo_filter_mode: Mapped[FilterMode] = mapped_column(
        Enum(FilterMode, native_enum=False, length=20), default=FilterMode.ALL
    )
    person_filter_mode: Mapped[FilterMode] = mapped_column(
        Enum(FilterMode, native_enum=False, length=20), default=FilterMode.ALL
    )
    sync_enabled: Mapped[bool] = mapped_column(default=True)
    # Hard filters: ignore_forks excludes forked repos everywhere,
    # members_only restricts every surface to persons linked to an org member.
    ignore_forks: Mapped[bool] = mapped_column(default=False)
    members_only: Mapped[bool] = mapped_column(default=False)
    sync_status: Mapped[str] = mapped_column(String(20), default="idle")
    sync_error: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    credential: Mapped[OrgCredential | None] = relationship(
        back_populates="org", cascade="all, delete-orphan", uselist=False
    )


class OrgCredential(Base):
    __tablename__ = "org_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), unique=True
    )
    token_encrypted: Mapped[str] = mapped_column(Text)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_snapshot: Mapped[dict | None] = mapped_column(JSONVariant)  # type: ignore[type-arg]

    org: Mapped[Org] = relationship(back_populates="credential")


class OrgMember(Base):
    """Membership snapshot, refreshed at sync time while members_only is on."""

    __tablename__ = "org_members"
    __table_args__ = (UniqueConstraint("org_id", "login_norm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    login: Mapped[str] = mapped_column(String(200))
    login_norm: Mapped[str] = mapped_column(String(200))
    node_id: Mapped[str | None] = mapped_column(String(100))


class RepoFilter(Base):
    __tablename__ = "repo_filters"
    __table_args__ = (UniqueConstraint("org_id", "repo_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    repo_name: Mapped[str] = mapped_column(String(300))


class PersonFilter(Base):
    __tablename__ = "person_filters"
    __table_args__ = (UniqueConstraint("org_id", "person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
