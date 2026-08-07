"""Ramp-up and turnover analytics."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.models import (
    Identity,
    IdentityKind,
    Org,
    Person,
    PersonRepoDayStats,
    PullRequest,
    Repo,
)
from gca.services import rampup


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


START = date(2026, 3, 2)
END = date(2026, 6, 1)


async def _seed(session: AsyncSession) -> tuple[Person, Person, Person]:
    org = Org(login="alpha")
    session.add(org)
    await session.flush()
    repo = Repo(org_id=org.id, name="core")
    joiner = Person(display_name="New")
    veteran = Person(display_name="Vet")
    leaver = Person(display_name="Gone")
    session.add_all([repo, joiner, veteran, leaver])
    await session.flush()
    ident = Identity(person_id=joiner.id, kind=IdentityKind.GITHUB_LOGIN, login="new")
    session.add(ident)
    await session.flush()

    def day(person: Person, when: date, sig: float) -> None:
        session.add(
            PersonRepoDayStats(
                person_id=person.id,
                repo_id=repo.id,
                org_id=org.id,
                day=when,
                commits=1,
                significance=sig,
            )
        )

    # Joiner: first activity 2026-03-09; ramps 1.0 -> 4.0 -> 5.0 weekly.
    day(joiner, date(2026, 3, 9), 1.0)
    day(joiner, date(2026, 3, 16), 4.0)
    day(joiner, date(2026, 3, 23), 5.0)
    # Veteran: active since long before, still active at period end.
    day(veteran, date(2025, 1, 6), 3.0)
    day(veteran, date(2026, 5, 28), 3.0)
    # Leaver: history before the period, last activity early in it.
    day(leaver, date(2025, 6, 2), 2.0)
    day(leaver, date(2026, 3, 12), 2.0)
    # Joiner's first merged PR three weeks after their first activity.
    session.add(
        PullRequest(
            repo_id=repo.id,
            number=1,
            author_identity_id=ident.id,
            title="First",
            state="closed",
            created_at_gh=datetime(2026, 3, 20, tzinfo=UTC),
            merged_at=datetime(2026, 3, 30, tzinfo=UTC),
        )
    )
    await session.flush()
    return joiner, veteran, leaver


async def test_cohort_classifies_joiners_and_leavers(session: AsyncSession) -> None:
    joiner, veteran, leaver = await _seed(session)
    cohort = await rampup.cohort(session, orgs=[], start=START, end=END)
    assert [e.person_id for e in cohort.joiners] == [joiner.id]
    assert [e.person_id for e in cohort.leavers] == [leaver.id]
    entry = cohort.joiners[0]
    # First activity 09/03, first merged PR 30/03: three weeks.
    assert entry.weeks_to_first_pr == 3
    # Median weekly significance is 4.0, first reached in week 1 after joining.
    assert entry.weeks_to_steady == 1
    assert entry.active_weeks == 3
    assert cohort.median_weeks_to_pr == 3
    assert cohort.measured == 1


async def test_veteran_still_active_is_neither(session: AsyncSession) -> None:
    _joiner, veteran, _leaver = await _seed(session)
    cohort = await rampup.cohort(session, orgs=[], start=START, end=END)
    tracked = {e.person_id for e in cohort.joiners} | {
        e.person_id for e in cohort.leavers
    }
    assert veteran.id not in tracked


async def test_person_ramp_facts(session: AsyncSession) -> None:
    joiner, _veteran, _leaver = await _seed(session)
    ramp = await rampup.person_ramp(session, person_id=joiner.id, orgs=[])
    assert ramp is not None
    assert ramp.first_day == date(2026, 3, 9)
    assert ramp.last_day == date(2026, 3, 23)
    assert ramp.weeks_to_first_pr == 3
    assert await rampup.person_ramp(session, person_id=99999, orgs=[]) is None
