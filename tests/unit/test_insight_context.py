"""Context builder contracts: windowed periods carry comparison keys,
all-time periods omit every one of them and say so explicitly."""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.models import Org, Person, PersonRepoDayStats, Repo
from gca.services import insight_context

COMPARISON_KEYS = {
    "previous_period",
    "previous_totals",
    "deltas_vs_previous",
    "people_turnover",
    "previous_metrics",
    "previous_rank",
    "rank_change",
    "previous_contributor_count",
    "contributor_turnover",
}


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[int, int]:
    org = Org(login="acme")
    session.add(org)
    await session.flush()
    repo = Repo(org_id=org.id, name="core")
    person = Person(display_name="Jane Doe")
    session.add_all([repo, person])
    await session.flush()
    session.add(
        PersonRepoDayStats(
            person_id=person.id,
            repo_id=repo.id,
            org_id=org.id,
            day=date(2026, 3, 10),
            commits=4,
            additions=100,
            significance=9.0,
        )
    )
    await session.flush()
    return person.id, repo.id


async def test_dashboard_context_windowed_carries_comparisons(
    session: AsyncSession,
) -> None:
    await _seed(session)
    context = await insight_context.dashboard_context(
        session,
        orgs=[],
        start=date(2026, 3, 1),
        end=date(2026, 4, 1),
        orgs_label="acme",
        period_label="March 2026",
    )
    assert context["period_mode"] == insight_context.WINDOW_MODE
    assert "previous_period" in context
    assert "previous_totals" in context
    assert "deltas_vs_previous" in context
    assert "people_turnover" in context
    assert "comparison" not in context


async def test_dashboard_context_all_time_omits_comparisons(
    session: AsyncSession,
) -> None:
    await _seed(session)
    context = await insight_context.dashboard_context(
        session,
        orgs=[],
        start=date(2008, 1, 1),
        end=date(2026, 4, 1),
        orgs_label="acme",
        period_label="All time",
        all_time=True,
    )
    assert context["period_mode"] == insight_context.ALL_TIME_MODE
    assert context["comparison"] == insight_context.ALL_TIME_NOTE
    assert not COMPARISON_KEYS & context.keys()


async def test_person_context_all_time_omits_comparisons(
    session: AsyncSession,
) -> None:
    person_id, _ = await _seed(session)
    context = await insight_context.person_context(
        session,
        person_id=person_id,
        display_name="Jane Doe",
        orgs=[],
        start=date(2008, 1, 1),
        end=date(2026, 4, 1),
        orgs_label="acme",
        period_label="All time",
        all_time=True,
    )
    assert context["period_mode"] == insight_context.ALL_TIME_MODE
    assert not COMPARISON_KEYS & context.keys()
    # Standing data stays present for the model.
    assert context["rank_by_significance"] == 1
    assert context["population"] == 1


async def test_repo_context_all_time_omits_comparisons(
    session: AsyncSession,
) -> None:
    _, repo_id = await _seed(session)
    context = await insight_context.repo_context(
        session,
        repo_id=repo_id,
        full_name="acme/core",
        orgs=[],
        start=date(2008, 1, 1),
        end=date(2026, 4, 1),
        orgs_label="acme",
        period_label="All time",
        all_time=True,
    )
    assert context["period_mode"] == insight_context.ALL_TIME_MODE
    assert not COMPARISON_KEYS & context.keys()
    assert context["contributor_count"] == 1
