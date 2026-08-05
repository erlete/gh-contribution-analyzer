"""Aggregate queries over daily rollups.

Single query layer shared by the dashboard, the detail views and the PDF
reports. Scope semantics: `orgs` is a list of org ids, empty list means all
orgs. Repo inclusion flags and per-org person filters are always applied.
Percentiles are percent ranks within the active population of the same scope
and range.
"""

from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.models import (
    FilterMode,
    Org,
    Person,
    PersonFilter,
    PersonRepoDayStats,
    Repo,
)

METRIC_FIELDS = (
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
class Totals:
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
    active_people: int = 0
    active_repos: int = 0

    @property
    def churn_ratio(self) -> float:
        return self.churn / self.additions if self.additions else 0.0


@dataclass
class PersonStat:
    person_id: int
    display_name: str
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
    percentiles: dict[str, float] = dc_field(default_factory=dict)

    @property
    def churn_ratio(self) -> float:
        return self.churn / self.additions if self.additions else 0.0


@dataclass
class RepoStat:
    repo_id: int
    name: str
    org_login: str
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    churn: int = 0
    significance: float = 0.0
    prs_opened: int = 0
    prs_merged: int = 0
    reviews: int = 0
    contributors: int = 0


@dataclass
class DayPoint:
    day: date
    commits: int
    additions: int
    deletions: int
    significance: float


async def _person_filter_map(
    session: AsyncSession, org_ids: list[int]
) -> dict[int, tuple[FilterMode, set[int]]]:
    org_query = sa.select(Org.id, Org.person_filter_mode)
    if org_ids:
        org_query = org_query.where(Org.id.in_(org_ids))
    modes = {
        row.id: row.person_filter_mode
        for row in (await session.execute(org_query)).all()
    }
    filter_query = sa.select(PersonFilter.org_id, PersonFilter.person_id)
    if org_ids:
        filter_query = filter_query.where(PersonFilter.org_id.in_(org_ids))
    listed: dict[int, set[int]] = {}
    for row in (await session.execute(filter_query)).all():
        listed.setdefault(row.org_id, set()).add(row.person_id)
    return {org_id: (mode, listed.get(org_id, set())) for org_id, mode in modes.items()}


def _person_allowed(
    filters: dict[int, tuple[FilterMode, set[int]]], org_id: int, person_id: int
) -> bool:
    mode, listed = filters.get(org_id, (FilterMode.ALL, set()))
    if mode == FilterMode.WHITELIST:
        return person_id in listed
    if mode == FilterMode.BLACKLIST:
        return person_id not in listed
    return True


def _base_rollup_query(
    orgs: list[int],
    start: date,
    end: date,
    repo_ids: list[int] | None,
    person_ids: list[int] | None,
) -> sa.Select[tuple[int, int, int, int, int, int, int, int, float, int, int, int]]:
    """Per (person, org) sums with repo inclusion applied."""
    q = (
        sa.select(
            PersonRepoDayStats.person_id,
            PersonRepoDayStats.org_id,
            sa.func.sum(PersonRepoDayStats.commits).label("commits"),
            sa.func.sum(PersonRepoDayStats.additions).label("additions"),
            sa.func.sum(PersonRepoDayStats.deletions).label("deletions"),
            sa.func.sum(PersonRepoDayStats.churn).label("churn"),
            sa.func.sum(PersonRepoDayStats.self_churn).label("self_churn"),
            sa.func.sum(PersonRepoDayStats.cross_churn).label("cross_churn"),
            sa.func.sum(PersonRepoDayStats.significance).label("significance"),
            sa.func.sum(PersonRepoDayStats.prs_opened).label("prs_opened"),
            sa.func.sum(PersonRepoDayStats.prs_merged).label("prs_merged"),
            sa.func.sum(PersonRepoDayStats.reviews).label("reviews"),
        )
        .join(Repo, PersonRepoDayStats.repo_id == Repo.id)
        .where(
            Repo.included.is_(True),
            PersonRepoDayStats.day >= start,
            PersonRepoDayStats.day < end,
        )
        .group_by(PersonRepoDayStats.person_id, PersonRepoDayStats.org_id)
    )
    if orgs:
        q = q.where(PersonRepoDayStats.org_id.in_(orgs))
    if repo_ids:
        q = q.where(PersonRepoDayStats.repo_id.in_(repo_ids))
    if person_ids:
        q = q.where(PersonRepoDayStats.person_id.in_(person_ids))
    return q


async def person_leaderboard(
    session: AsyncSession,
    *,
    orgs: list[int],
    start: date,
    end: date,
    repo_ids: list[int] | None = None,
    person_ids: list[int] | None = None,
) -> list[PersonStat]:
    filters = await _person_filter_map(session, orgs)
    rows = (
        await session.execute(
            _base_rollup_query(orgs, start, end, repo_ids, person_ids)
        )
    ).all()
    names = {
        row.id: row.display_name
        for row in (
            await session.execute(sa.select(Person.id, Person.display_name))
        ).all()
    }
    combined: dict[int, PersonStat] = {}
    for row in rows:
        if not _person_allowed(filters, row.org_id, row.person_id):
            continue
        stat = combined.setdefault(
            row.person_id,
            PersonStat(
                person_id=row.person_id,
                display_name=names.get(row.person_id, f"person {row.person_id}"),
            ),
        )
        for field_name in METRIC_FIELDS:
            current = getattr(stat, field_name)
            setattr(stat, field_name, current + (getattr(row, field_name) or 0))
    result = sorted(combined.values(), key=lambda s: s.significance, reverse=True)
    add_percentiles(result)
    return result


def add_percentiles(stats: list[PersonStat]) -> None:
    """Percent rank per metric across the given population, in place."""
    n = len(stats)
    if n == 0:
        return
    for metric in METRIC_FIELDS:
        ordered = sorted(stats, key=lambda s: getattr(s, metric))
        values = [getattr(s, metric) for s in ordered]
        for stat in stats:
            value = getattr(stat, metric)
            below = sum(1 for v in values if v < value)
            if n > 1:
                stat.percentiles[metric] = below / (n - 1)
            else:
                stat.percentiles[metric] = 1.0 if value > 0 else 0.0


async def totals(
    session: AsyncSession,
    *,
    orgs: list[int],
    start: date,
    end: date,
    repo_ids: list[int] | None = None,
    person_ids: list[int] | None = None,
) -> Totals:
    people = await person_leaderboard(
        session,
        orgs=orgs,
        start=start,
        end=end,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    result = Totals(active_people=len(people))
    for stat in people:
        for field_name in METRIC_FIELDS:
            setattr(
                result,
                field_name,
                getattr(result, field_name) + getattr(stat, field_name),
            )
    repo_count_q = (
        sa.select(sa.func.count(sa.distinct(PersonRepoDayStats.repo_id)))
        .join(Repo, PersonRepoDayStats.repo_id == Repo.id)
        .where(
            Repo.included.is_(True),
            PersonRepoDayStats.day >= start,
            PersonRepoDayStats.day < end,
        )
    )
    if orgs:
        repo_count_q = repo_count_q.where(PersonRepoDayStats.org_id.in_(orgs))
    if repo_ids:
        repo_count_q = repo_count_q.where(PersonRepoDayStats.repo_id.in_(repo_ids))
    result.active_repos = (await session.execute(repo_count_q)).scalar_one()
    return result


async def repo_leaderboard(
    session: AsyncSession,
    *,
    orgs: list[int],
    start: date,
    end: date,
    repo_ids: list[int] | None = None,
    person_ids: list[int] | None = None,
) -> list[RepoStat]:
    filters = await _person_filter_map(session, orgs)
    q = (
        sa.select(
            PersonRepoDayStats.repo_id,
            PersonRepoDayStats.org_id,
            PersonRepoDayStats.person_id,
            Repo.name,
            Org.login.label("org_login"),
            sa.func.sum(PersonRepoDayStats.commits).label("commits"),
            sa.func.sum(PersonRepoDayStats.additions).label("additions"),
            sa.func.sum(PersonRepoDayStats.deletions).label("deletions"),
            sa.func.sum(PersonRepoDayStats.churn).label("churn"),
            sa.func.sum(PersonRepoDayStats.significance).label("significance"),
            sa.func.sum(PersonRepoDayStats.prs_opened).label("prs_opened"),
            sa.func.sum(PersonRepoDayStats.prs_merged).label("prs_merged"),
            sa.func.sum(PersonRepoDayStats.reviews).label("reviews"),
        )
        .join(Repo, PersonRepoDayStats.repo_id == Repo.id)
        .join(Org, PersonRepoDayStats.org_id == Org.id)
        .where(
            Repo.included.is_(True),
            PersonRepoDayStats.day >= start,
            PersonRepoDayStats.day < end,
        )
        .group_by(
            PersonRepoDayStats.repo_id,
            PersonRepoDayStats.org_id,
            PersonRepoDayStats.person_id,
            Repo.name,
            Org.login,
        )
    )
    if orgs:
        q = q.where(PersonRepoDayStats.org_id.in_(orgs))
    if repo_ids:
        q = q.where(PersonRepoDayStats.repo_id.in_(repo_ids))
    if person_ids:
        q = q.where(PersonRepoDayStats.person_id.in_(person_ids))
    combined: dict[int, RepoStat] = {}
    contributors: dict[int, set[int]] = {}
    for row in (await session.execute(q)).all():
        if not _person_allowed(filters, row.org_id, row.person_id):
            continue
        stat = combined.setdefault(
            row.repo_id,
            RepoStat(repo_id=row.repo_id, name=row.name, org_login=row.org_login),
        )
        for field_name in (
            "commits",
            "additions",
            "deletions",
            "churn",
            "significance",
            "prs_opened",
            "prs_merged",
            "reviews",
        ):
            setattr(
                stat,
                field_name,
                getattr(stat, field_name) + (getattr(row, field_name) or 0),
            )
        contributors.setdefault(row.repo_id, set()).add(row.person_id)
    for repo_id, stat in combined.items():
        stat.contributors = len(contributors.get(repo_id, set()))
    return sorted(combined.values(), key=lambda s: s.significance, reverse=True)


async def timeseries(
    session: AsyncSession,
    *,
    orgs: list[int],
    start: date,
    end: date,
    repo_ids: list[int] | None = None,
    person_ids: list[int] | None = None,
) -> list[DayPoint]:
    filters = await _person_filter_map(session, orgs)
    q = (
        sa.select(
            PersonRepoDayStats.day,
            PersonRepoDayStats.org_id,
            PersonRepoDayStats.person_id,
            sa.func.sum(PersonRepoDayStats.commits).label("commits"),
            sa.func.sum(PersonRepoDayStats.additions).label("additions"),
            sa.func.sum(PersonRepoDayStats.deletions).label("deletions"),
            sa.func.sum(PersonRepoDayStats.significance).label("significance"),
        )
        .join(Repo, PersonRepoDayStats.repo_id == Repo.id)
        .where(
            Repo.included.is_(True),
            PersonRepoDayStats.day >= start,
            PersonRepoDayStats.day < end,
        )
        .group_by(
            PersonRepoDayStats.day,
            PersonRepoDayStats.org_id,
            PersonRepoDayStats.person_id,
        )
        .order_by(PersonRepoDayStats.day)
    )
    if orgs:
        q = q.where(PersonRepoDayStats.org_id.in_(orgs))
    if repo_ids:
        q = q.where(PersonRepoDayStats.repo_id.in_(repo_ids))
    if person_ids:
        q = q.where(PersonRepoDayStats.person_id.in_(person_ids))
    days: dict[date, DayPoint] = {}
    for row in (await session.execute(q)).all():
        if not _person_allowed(filters, row.org_id, row.person_id):
            continue
        point = days.setdefault(
            row.day,
            DayPoint(
                day=row.day, commits=0, additions=0, deletions=0, significance=0.0
            ),
        )
        point.commits += row.commits or 0
        point.additions += row.additions or 0
        point.deletions += row.deletions or 0
        point.significance += row.significance or 0.0
    return [days[d] for d in sorted(days)]


async def person_repo_split(
    session: AsyncSession,
    *,
    person_id: int,
    orgs: list[int],
    start: date,
    end: date,
) -> list[RepoStat]:
    return await repo_leaderboard(
        session, orgs=orgs, start=start, end=end, person_ids=[person_id]
    )


async def repo_contributors(
    session: AsyncSession,
    *,
    repo_id: int,
    orgs: list[int],
    start: date,
    end: date,
) -> list[PersonStat]:
    return await person_leaderboard(
        session, orgs=orgs, start=start, end=end, repo_ids=[repo_id]
    )
