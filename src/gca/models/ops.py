"""Operational tables: sync runs, job ledger, settings, insights, reports, mail."""

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gca.db.base import Base, JSONVariant


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    repo_id: Mapped[int | None] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict | None] = mapped_column(JSONVariant)  # type: ignore[type-arg]
    error: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    """One operational fact: who did what to which subject, when.

    Everything user- or system-initiated that changes state records one row:
    syncs, report lifecycles, mail sendings, org and policy changes,
    identity merges. The operations page reads this table.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    actor: Mapped[str] = mapped_column(String(20), default="admin")
    kind: Mapped[str] = mapped_column(String(60), index=True)
    subject: Mapped[str] = mapped_column(String(300), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict | None] = mapped_column(JSONVariant)  # type: ignore[type-arg]


class JobLedger(Base):
    __tablename__ = "job_ledger"
    __table_args__ = (UniqueConstraint("job_key", "period_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_key: Mapped[str] = mapped_column(String(100))
    period_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="done")
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSONVariant)  # type: ignore[type-arg]


class Insight(Base):
    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(80), unique=True)
    view: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Recipient(Base):
    __tablename__ = "recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    active: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str | None] = mapped_column(String(300))


schedule_recipients = Table(
    "schedule_recipients",
    Base.metadata,
    Column(
        "schedule_id",
        ForeignKey("report_schedules.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "recipient_id",
        ForeignKey("recipients.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class ReportSchedule(Base):
    __tablename__ = "report_schedules"
    __table_args__ = (UniqueConstraint("period_kind", "report_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period_kind: Mapped[str] = mapped_column(String(20))
    report_kind: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(default=False)
    org_scope: Mapped[list | None] = mapped_column(JSONVariant)  # type: ignore[type-arg]

    recipients: Mapped[list[Recipient]] = relationship(secondary=schedule_recipients)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300), default="")
    period_kind: Mapped[str] = mapped_column(String(20), default="custom")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    org_scope: Mapped[list | None] = mapped_column(JSONVariant)  # type: ignore[type-arg]
    subject_type: Mapped[str | None] = mapped_column(String(20))
    subject_id: Mapped[int | None] = mapped_column()
    # Generation request filters (repo_ids, person_ids) for queued reports.
    params: Mapped[dict | None] = mapped_column(JSONVariant)  # type: ignore[type-arg]
    pdf_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="generated")
    error: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
