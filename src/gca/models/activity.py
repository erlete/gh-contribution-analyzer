"""Commits, commit files, pull requests and reviews."""

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gca.db.base import Base


class FileClass(enum.StrEnum):
    CODE = "code"
    TESTS = "tests"
    CONFIG = "config"
    DOCS = "docs"
    GENERATED = "generated"


class Commit(Base):
    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("repo_id", "oid"),
        Index("ix_commits_repo_authored", "repo_id", "authored_at"),
        Index("ix_commits_author_authored", "author_identity_id", "authored_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    oid: Mapped[str] = mapped_column(String(64))
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    author_identity_id: Mapped[int] = mapped_column(
        ForeignKey("identities.id", ondelete="RESTRICT")
    )
    message_subject: Mapped[str] = mapped_column(String(300), default="")
    additions: Mapped[int] = mapped_column(default=0)
    deletions: Mapped[int] = mapped_column(default=0)
    files_changed: Mapped[int] = mapped_column(default=0)
    is_merge: Mapped[bool] = mapped_column(default=False)
    is_mechanical: Mapped[bool] = mapped_column(default=False)
    significance: Mapped[float] = mapped_column(Float, default=0.0)
    churn_lines: Mapped[int] = mapped_column(default=0)
    self_churn_lines: Mapped[int] = mapped_column(default=0)
    cross_churn_lines: Mapped[int] = mapped_column(default=0)

    files: Mapped[list[CommitFile]] = relationship(
        back_populates="commit", cascade="all, delete-orphan"
    )


class CommitFile(Base):
    __tablename__ = "commit_files"
    __table_args__ = (
        Index("ix_commit_files_repo_path_authored", "repo_id", "path", "authored_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    commit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("commits.id", ondelete="CASCADE"), index=True
    )
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    path: Mapped[str] = mapped_column(String(1024))
    old_path: Mapped[str | None] = mapped_column(String(1024))
    additions: Mapped[int] = mapped_column(default=0)
    deletions: Mapped[int] = mapped_column(default=0)
    file_class: Mapped[FileClass] = mapped_column(
        Enum(FileClass, native_enum=False, length=20), default=FileClass.CODE
    )

    commit: Mapped[Commit] = relationship(back_populates="files")


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repo_id", "number"),
        Index("ix_pull_requests_repo_created", "repo_id", "created_at_gh"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column()
    node_id: Mapped[str | None] = mapped_column(String(100))
    author_identity_id: Mapped[int | None] = mapped_column(
        ForeignKey("identities.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(500), default="")
    state: Mapped[str] = mapped_column(String(10), default="open")
    created_at_gh: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    additions: Mapped[int] = mapped_column(default=0)
    deletions: Mapped[int] = mapped_column(default=0)
    changed_files: Mapped[int] = mapped_column(default=0)

    reviews: Mapped[list[Review]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        Index("ix_reviews_reviewer_submitted", "reviewer_identity_id", "submitted_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pull_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    reviewer_identity_id: Mapped[int | None] = mapped_column(
        ForeignKey("identities.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(30), default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pull_request: Mapped[PullRequest] = relationship(back_populates="reviews")
