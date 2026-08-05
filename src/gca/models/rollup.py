"""Daily-grain rollups powering every dashboard aggregate and report."""

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from gca.db.base import Base


class PersonRepoDayStats(Base):
    __tablename__ = "person_repo_day_stats"
    __table_args__ = (
        Index("ix_prds_org_day", "org_id", "day"),
        Index("ix_prds_repo_day", "repo_id", "day"),
    )

    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True
    )
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    commits: Mapped[int] = mapped_column(default=0)
    additions: Mapped[int] = mapped_column(default=0)
    deletions: Mapped[int] = mapped_column(default=0)
    churn: Mapped[int] = mapped_column(default=0)
    self_churn: Mapped[int] = mapped_column(default=0)
    cross_churn: Mapped[int] = mapped_column(default=0)
    significance: Mapped[float] = mapped_column(Float, default=0.0)
    prs_opened: Mapped[int] = mapped_column(default=0)
    prs_merged: Mapped[int] = mapped_column(default=0)
    reviews: Mapped[int] = mapped_column(default=0)
