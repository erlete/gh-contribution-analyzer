"""Repositories and their clone/sync state."""

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gca.db.base import Base
from gca.models.org import Org


class CloneStatus(enum.StrEnum):
    PENDING = "pending"
    CLONING = "cloning"
    READY = "ready"
    ERROR = "error"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    is_private: Mapped[bool] = mapped_column(default=False)
    is_archived: Mapped[bool] = mapped_column(default=False)
    is_fork: Mapped[bool] = mapped_column(default=False)
    included: Mapped[bool] = mapped_column(default=True, index=True)
    clone_status: Mapped[CloneStatus] = mapped_column(
        Enum(CloneStatus, native_enum=False, length=20), default=CloneStatus.PENDING
    )
    clone_error: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ingested_oid: Mapped[str | None] = mapped_column(String(64))
    pr_cursor: Mapped[str | None] = mapped_column(String(200))
    pr_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    org: Mapped[Org] = relationship()
