"""Daily rollup maintenance.

`person_repo_day_stats` is the only table dashboards and reports aggregate
over. Deltas are applied incrementally during ingest; merges and unmerges
recompute affected persons from the base tables.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.models import Commit, Identity, PersonRepoDayStats, PullRequest, Repo, Review

_NUMERIC = (
    "commits",
    "additions",
    "deletions",
    "churn",
    "self_churn",
    "cross_churn",
    "significance",
    "prs_opened",
    "prs_merged",
    "reviews",
)


@dataclass
class RollupDelta:
    person_id: int
    repo_id: int
    org_id: int
    day: date
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    churn: int = 0
    self_churn: int = 0
    cross_churn: int = 0
    significance: float = 0.0
    prs_opened: int = 0
    prs_merged: int = 0
    reviews: int = 0


def day_of(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).date()


def merge_deltas(deltas: list[RollupDelta]) -> list[RollupDelta]:
    combined: dict[tuple[int, int, date], RollupDelta] = {}
    for d in deltas:
        key = (d.person_id, d.repo_id, d.day)
        if key not in combined:
            combined[key] = RollupDelta(
                person_id=d.person_id, repo_id=d.repo_id, org_id=d.org_id, day=d.day
            )
        target = combined[key]
        for f in _NUMERIC:
            setattr(target, f, getattr(target, f) + getattr(d, f))
    return list(combined.values())


async def apply_deltas(
    session: AsyncSession, deltas: list[RollupDelta], *, additive: bool = True
) -> None:
    deltas = merge_deltas(deltas)
    if not deltas:
        return
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    table = PersonRepoDayStats.__table__
    stmt = insert(PersonRepoDayStats).values([asdict(d) for d in deltas])
    if additive:
        updates = {
            name: getattr(table.c, name) + getattr(stmt.excluded, name)
            for name in _NUMERIC
        }
    else:
        updates = {name: getattr(stmt.excluded, name) for name in _NUMERIC}
    stmt = stmt.on_conflict_do_update(
        index_elements=["person_id", "repo_id", "day"], set_=updates
    )
    await session.execute(stmt)


async def recompute_for_persons(session: AsyncSession, person_ids: list[int]) -> None:
    """Rebuild rollups for the given persons from commits, PRs and reviews."""
    if not person_ids:
        return
    await session.execute(
        sa.delete(PersonRepoDayStats).where(
            PersonRepoDayStats.person_id.in_(person_ids)
        )
    )
    deltas: list[RollupDelta] = []

    commit_rows = (
        await session.execute(
            sa.select(
                Identity.person_id,
                Commit.repo_id,
                Repo.org_id,
                Commit.authored_at,
                Commit.additions,
                Commit.deletions,
                Commit.significance,
                Commit.churn_lines,
                Commit.self_churn_lines,
                Commit.cross_churn_lines,
            )
            .join(Identity, Commit.author_identity_id == Identity.id)
            .join(Repo, Commit.repo_id == Repo.id)
            .where(Identity.person_id.in_(person_ids))
        )
    ).all()
    for row in commit_rows:
        deltas.append(
            RollupDelta(
                person_id=row.person_id,
                repo_id=row.repo_id,
                org_id=row.org_id,
                day=day_of(row.authored_at),
                commits=1,
                additions=row.additions,
                deletions=row.deletions,
                significance=row.significance,
                churn=row.churn_lines,
                self_churn=row.self_churn_lines,
                cross_churn=row.cross_churn_lines,
            )
        )

    pr_rows = (
        await session.execute(
            sa.select(
                Identity.person_id,
                PullRequest.repo_id,
                Repo.org_id,
                PullRequest.created_at_gh,
                PullRequest.merged_at,
            )
            .join(Identity, PullRequest.author_identity_id == Identity.id)
            .join(Repo, PullRequest.repo_id == Repo.id)
            .where(Identity.person_id.in_(person_ids))
        )
    ).all()
    for pr_row in pr_rows:
        deltas.append(
            RollupDelta(
                person_id=pr_row.person_id,
                repo_id=pr_row.repo_id,
                org_id=pr_row.org_id,
                day=day_of(pr_row.created_at_gh),
                prs_opened=1,
            )
        )
        if pr_row.merged_at is not None:
            deltas.append(
                RollupDelta(
                    person_id=pr_row.person_id,
                    repo_id=pr_row.repo_id,
                    org_id=pr_row.org_id,
                    day=day_of(pr_row.merged_at),
                    prs_merged=1,
                )
            )

    review_rows = (
        await session.execute(
            sa.select(
                Identity.person_id,
                PullRequest.repo_id,
                Repo.org_id,
                Review.submitted_at,
            )
            .join(Identity, Review.reviewer_identity_id == Identity.id)
            .join(PullRequest, Review.pull_request_id == PullRequest.id)
            .join(Repo, PullRequest.repo_id == Repo.id)
            .where(Identity.person_id.in_(person_ids), Review.submitted_at.is_not(None))
        )
    ).all()
    for review_row in review_rows:
        if review_row.submitted_at is None:
            continue
        deltas.append(
            RollupDelta(
                person_id=review_row.person_id,
                repo_id=review_row.repo_id,
                org_id=review_row.org_id,
                day=day_of(review_row.submitted_at),
                reviews=1,
            )
        )

    await apply_deltas(session, deltas, additive=False)


__all__ = [
    "RollupDelta",
    "apply_deltas",
    "day_of",
    "merge_deltas",
    "recompute_for_persons",
]
