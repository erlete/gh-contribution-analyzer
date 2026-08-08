"""Drill-down: the raw commits and pull requests behind an aggregate.

Every aggregate row on the person and repository pages is a person times
repository pair, so both ids are always required. Rows link straight to
GitHub so every number in the app can be traced to its source.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.models import Commit, Identity, Org, PullRequest, Repo


@dataclass
class CommitRow:
    short: str
    subject: str
    when: str
    additions: int
    deletions: int
    significance: float
    url: str


@dataclass
class PrRow:
    number: int
    title: str
    state: str
    created: str
    merged: str
    url: str


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time.min, tzinfo=UTC),
        datetime.combine(end, time.min, tzinfo=UTC),
    )


async def _repo_slug(session: AsyncSession, repo_id: int) -> str:
    row = (
        await session.execute(
            sa.select(Org.login, Repo.name)
            .join(Repo, Repo.org_id == Org.id)
            .where(Repo.id == repo_id)
        )
    ).one()
    return f"{row.login}/{row.name}"


async def commit_rows(
    session: AsyncSession,
    *,
    person_id: int,
    repo_id: int,
    start: date,
    end: date,
    limit: int,
) -> tuple[list[CommitRow], int]:
    """Newest-first commits for the pair, plus the total count in period."""
    lo, hi = _bounds(start, end)
    slug = await _repo_slug(session, repo_id)
    base = (
        sa.select(Commit)
        .join(Identity, Commit.author_identity_id == Identity.id)
        .where(
            Commit.repo_id == repo_id,
            Identity.person_id == person_id,
            Commit.authored_at >= lo,
            Commit.authored_at < hi,
        )
    )
    total = (
        await session.execute(sa.select(sa.func.count()).select_from(base.subquery()))
    ).scalar_one()
    commits = (
        (await session.execute(base.order_by(Commit.authored_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    rows = [
        CommitRow(
            short=c.oid[:9],
            subject=c.message_subject or "(no subject)",
            when=c.authored_at.strftime("%d/%m/%Y"),
            additions=c.additions,
            deletions=c.deletions,
            significance=round(c.significance, 1),
            url=f"https://github.com/{slug}/commit/{c.oid}",
        )
        for c in commits
    ]
    return rows, int(total)


async def pr_rows(
    session: AsyncSession,
    *,
    person_id: int,
    repo_id: int,
    start: date,
    end: date,
    limit: int,
) -> tuple[list[PrRow], int]:
    """Newest-first pull requests opened by the person in the repo."""
    lo, hi = _bounds(start, end)
    slug = await _repo_slug(session, repo_id)
    base = (
        sa.select(PullRequest)
        .join(Identity, PullRequest.author_identity_id == Identity.id)
        .where(
            PullRequest.repo_id == repo_id,
            Identity.person_id == person_id,
            PullRequest.created_at_gh >= lo,
            PullRequest.created_at_gh < hi,
        )
    )
    total = (
        await session.execute(sa.select(sa.func.count()).select_from(base.subquery()))
    ).scalar_one()
    prs = (
        (
            await session.execute(
                base.order_by(PullRequest.created_at_gh.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    rows = [
        PrRow(
            number=p.number,
            title=p.title or "(untitled)",
            state="merged" if p.merged_at else p.state,
            created=p.created_at_gh.strftime("%d/%m/%Y"),
            merged=p.merged_at.strftime("%d/%m/%Y") if p.merged_at else "",
            url=f"https://github.com/{slug}/pull/{p.number}",
        )
        for p in prs
    ]
    return rows, int(total)
