from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.models import (
    FilterMode,
    Org,
    Person,
    PersonFilter,
    PersonRepoDayStats,
    Repo,
)
from gca.services import stats


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[Org, Org, Repo, Repo, list[Person]]:
    org_a = Org(login="alpha")
    org_b = Org(login="beta")
    session.add_all([org_a, org_b])
    await session.flush()
    repo_a = Repo(org_id=org_a.id, name="core")
    repo_b = Repo(org_id=org_b.id, name="site")
    repo_x = Repo(org_id=org_a.id, name="excluded", included=False)
    session.add_all([repo_a, repo_b, repo_x])
    persons = [Person(display_name=f"P{i}") for i in range(3)]
    session.add_all(persons)
    await session.flush()

    def row(person: Person, repo: Repo, org: Org, day: date, **kw: object) -> None:
        session.add(
            PersonRepoDayStats(
                person_id=person.id,
                repo_id=repo.id,
                org_id=org.id,
                day=day,
                **kw,  # type: ignore[arg-type]
            )
        )

    d = date(2026, 3, 10)
    row(persons[0], repo_a, org_a, d, commits=10, additions=100, significance=20.0)
    row(
        persons[1],
        repo_a,
        org_a,
        d,
        commits=5,
        additions=50,
        significance=10.0,
        churn=25,
    )
    row(persons[1], repo_b, org_b, d, commits=3, additions=30, significance=6.0)
    row(
        persons[2],
        repo_b,
        org_b,
        d,
        commits=1,
        additions=10,
        significance=1.0,
        reviews=4,
    )
    row(persons[0], repo_x, org_a, d, commits=99, additions=999, significance=99.0)
    await session.flush()
    return org_a, org_b, repo_a, repo_b, persons


async def test_leaderboard_scope_and_excluded_repo(session: AsyncSession) -> None:
    org_a, org_b, _, _, persons = await _seed(session)
    all_orgs = await stats.person_leaderboard(
        session, orgs=[], start=date(2026, 3, 1), end=date(2026, 4, 1)
    )
    # Excluded repo rows never count.
    top = all_orgs[0]
    assert top.person_id == persons[0].id
    assert top.commits == 10
    assert len(all_orgs) == 3

    only_b = await stats.person_leaderboard(
        session, orgs=[org_b.id], start=date(2026, 3, 1), end=date(2026, 4, 1)
    )
    assert {s.person_id for s in only_b} == {persons[1].id, persons[2].id}
    p1 = next(s for s in only_b if s.person_id == persons[1].id)
    assert p1.commits == 3


async def test_percentiles(session: AsyncSession) -> None:
    await _seed(session)
    board = await stats.person_leaderboard(
        session, orgs=[], start=date(2026, 3, 1), end=date(2026, 4, 1)
    )
    top = board[0]
    bottom = board[-1]
    assert top.percentiles["significance"] == 1.0
    assert bottom.percentiles["significance"] == 0.0
    assert bottom.percentiles["reviews"] == 1.0


async def test_person_blacklist(session: AsyncSession) -> None:
    org_a, _, _, _, persons = await _seed(session)
    org_a.person_filter_mode = FilterMode.BLACKLIST
    session.add(PersonFilter(org_id=org_a.id, person_id=persons[0].id))
    await session.flush()
    board = await stats.person_leaderboard(
        session, orgs=[org_a.id], start=date(2026, 3, 1), end=date(2026, 4, 1)
    )
    assert {s.person_id for s in board} == {persons[1].id}


async def test_totals_and_repo_board(session: AsyncSession) -> None:
    _, _, repo_a, _, persons = await _seed(session)
    result = await stats.totals(
        session, orgs=[], start=date(2026, 3, 1), end=date(2026, 4, 1)
    )
    assert result.commits == 19
    assert result.active_people == 3
    assert result.active_repos == 2
    assert result.churn == 25
    assert 0 < result.churn_ratio < 1

    repos = await stats.repo_leaderboard(
        session, orgs=[], start=date(2026, 3, 1), end=date(2026, 4, 1)
    )
    assert [r.name for r in repos][0] == "core"
    core = repos[0]
    assert core.contributors == 2

    series = await stats.timeseries(
        session, orgs=[], start=date(2026, 3, 1), end=date(2026, 4, 1)
    )
    assert len(series) == 1
    assert series[0].commits == 19
