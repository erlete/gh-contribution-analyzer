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
    Review,
)
from gca.services import research, stats


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


START = date(2026, 3, 2)  # a Monday
END = date(2026, 3, 30)


async def _seed(session: AsyncSession) -> tuple[Org, Repo, Repo, list[Person]]:
    org = Org(login="alpha")
    session.add(org)
    await session.flush()
    repo_a = Repo(org_id=org.id, name="core")
    repo_b = Repo(org_id=org.id, name="site")
    repo_x = Repo(org_id=org.id, name="junk", included=False)
    session.add_all([repo_a, repo_b, repo_x])
    persons = [Person(display_name=f"P{i}") for i in range(3)]
    session.add_all(persons)
    await session.flush()

    def row(person: Person, repo: Repo, day: date, **kw: object) -> None:
        session.add(
            PersonRepoDayStats(
                person_id=person.id,
                repo_id=repo.id,
                org_id=org.id,
                day=day,
                **kw,  # type: ignore[arg-type]
            )
        )

    # Week 1: P0 heavy in core, P1 light in core and site.
    row(
        persons[0],
        repo_a,
        date(2026, 3, 3),
        commits=10,
        additions=100,
        significance=20.0,
    )
    row(persons[1], repo_a, date(2026, 3, 4), commits=2, additions=20, significance=4.0)
    row(persons[1], repo_b, date(2026, 3, 5), commits=3, additions=30, significance=6.0)
    # Week 3 (second half): P1 takes over core; P0 quiet.
    row(
        persons[1],
        repo_a,
        date(2026, 3, 24),
        commits=8,
        additions=80,
        significance=16.0,
    )
    row(
        persons[2], repo_b, date(2026, 3, 25), commits=1, additions=10, significance=1.0
    )
    # Excluded repo activity must never count anywhere.
    row(
        persons[0],
        repo_x,
        date(2026, 3, 10),
        commits=50,
        additions=500,
        significance=50.0,
    )
    await session.flush()
    return org, repo_a, repo_b, persons


def test_validate_block_rules() -> None:
    assert research.validate_block({"op": "nope"}) == "Unknown operation."
    assert research.validate_block({"op": "compare_people", "people": [1]}) is not None
    assert research.validate_block({"op": "compare_people", "people": [1, 2]}) is None
    assert research.validate_block({"op": "rank", "entity": "cats"}) is not None
    assert (
        research.validate_block(
            {"op": "rank", "entity": "people", "people": [1, 2], "metric": "commits"}
        )
        is None
    )
    assert (
        research.validate_block({"op": "correlate_people", "person": 1, "person_b": 1})
        == "Pick two different people."
    )
    assert (
        research.validate_block(
            {"op": "volatility", "entity": "repos", "repos": [5], "metric": "bogus"}
        )
        == "Pick a valid metric."
    )


async def test_weekly_by_entity_buckets_and_exclusions(
    session: AsyncSession,
) -> None:
    _org, repo_a, _repo_b, persons = await _seed(session)
    weekly = await stats.weekly_by_entity(
        session, orgs=[], start=START, end=END, by="person"
    )
    p0 = weekly[persons[0].id]
    # All P0 included work lands in week 1 (Monday 2026-03-02); the excluded
    # repo's 50 commits never appear.
    assert list(p0) == [date(2026, 3, 2)]
    assert p0[date(2026, 3, 2)]["commits"] == 10
    p1 = weekly[persons[1].id]
    assert p1[date(2026, 3, 2)]["significance"] == 10.0
    assert p1[date(2026, 3, 23)]["significance"] == 16.0
    by_repo = await stats.weekly_by_entity(
        session, orgs=[], start=START, end=END, by="repo", repo_ids=[repo_a.id]
    )
    assert by_repo[repo_a.id][date(2026, 3, 2)]["commits"] == 12


async def test_compare_people_block(session: AsyncSession) -> None:
    _org, _repo_a, _repo_b, persons = await _seed(session)
    result = await research.run_block(
        session,
        {"op": "compare_people", "people": [persons[0].id, persons[1].id]},
        orgs=[],
        start=START,
        end=END,
        period_label="March window",
        all_time=False,
    )
    assert result.error is None
    assert "P0" in result.sentence and "P1" in result.sentence
    table = result.tables[0]
    assert table.headers[0] == ("Metric",)
    assert [h[0] for h in table.headers[1:]] == ["P0", "P1"]
    commits_row = next(r for r in table.rows if r[0].text == "Commits")
    assert commits_row[1].text == "10"
    assert commits_row[2].text == "13"
    assert len(result.charts) == 1
    assert [s.name for s in result.charts[0].series] == ["P0", "P1"]
    assert result.context["period_mode"] == "window"
    assert result.context["facts"]


async def test_rank_block_orders_and_shares(session: AsyncSession) -> None:
    _org, _repo_a, _repo_b, persons = await _seed(session)
    result = await research.run_block(
        session,
        {
            "op": "rank",
            "entity": "people",
            "people": [p.id for p in persons],
            "metric": "commits",
        },
        orgs=[],
        start=START,
        end=END,
        period_label="March window",
        all_time=False,
    )
    assert result.error is None
    rows = result.tables[0].rows
    assert [r[1].text for r in rows] == ["P1", "P0", "P2"]
    assert rows[0][2].text == "13"
    assert rows[0][3].text == "54%"


async def test_overlap_people_intersection(session: AsyncSession) -> None:
    _org, repo_a, _repo_b, persons = await _seed(session)
    result = await research.run_block(
        session,
        {"op": "overlap_people", "people": [persons[0].id, persons[1].id]},
        orgs=[],
        start=START,
        end=END,
        period_label="March window",
        all_time=False,
    )
    assert result.error is None
    rows = result.tables[0].rows
    # Only core saw both P0 and P1.
    assert len(rows) == 1
    assert rows[0][0].text == "alpha/core"
    assert rows[0][0].link == f"/repos/{repo_a.id}"


async def test_handoff_detects_shift(session: AsyncSession) -> None:
    _org, repo_a, _repo_b, persons = await _seed(session)
    result = await research.run_block(
        session,
        {"op": "handoff", "repo": repo_a.id},
        orgs=[],
        start=START,
        end=END,
        period_label="March window",
        all_time=False,
    )
    assert result.error is None
    # First half of core: P0 83%, P1 17%. Second half: P1 100%. The largest
    # shift is 83 points either way.
    top = result.tables[0].rows[0]
    assert top[0].text in ("P0", "P1")
    assert top[3].text in ("+83 pts", "-83 pts")
    assert any("picked up" in f or "handed off" in f for f in result.context["facts"])


async def test_review_graph_counts_and_heat(session: AsyncSession) -> None:
    org, repo_a, _repo_b, persons = await _seed(session)
    ident_0 = Identity(
        person_id=persons[0].id, kind=IdentityKind.GITHUB_LOGIN, login="p0"
    )
    ident_1 = Identity(
        person_id=persons[1].id, kind=IdentityKind.GITHUB_LOGIN, login="p1"
    )
    session.add_all([ident_0, ident_1])
    await session.flush()
    pr = PullRequest(
        repo_id=repo_a.id,
        number=1,
        author_identity_id=ident_1.id,
        created_at_gh=datetime(2026, 3, 10, tzinfo=UTC),
    )
    session.add(pr)
    await session.flush()
    session.add_all(
        [
            Review(
                pull_request_id=pr.id,
                reviewer_identity_id=ident_0.id,
                submitted_at=datetime(2026, 3, 11, tzinfo=UTC),
            ),
            Review(
                pull_request_id=pr.id,
                reviewer_identity_id=ident_0.id,
                submitted_at=datetime(2026, 3, 12, tzinfo=UTC),
            ),
        ]
    )
    await session.flush()
    result = await research.run_block(
        session,
        {"op": "review_graph", "people": [persons[0].id, persons[1].id]},
        orgs=[],
        start=START,
        end=END,
        period_label="March window",
        all_time=False,
    )
    assert result.error is None
    rows = result.tables[0].rows
    # Row P0, column P1 carries both reviews at full heat.
    assert rows[0][2].text == "2"
    assert rows[0][2].heat == 1.0
    assert rows[1][1].text == "0"
    assert "2 reviews were exchanged" in result.context["facts"][0]


async def test_invalid_block_yields_error_result(session: AsyncSession) -> None:
    result = await research.run_block(
        session,
        {"op": "compare_people", "people": [1]},
        orgs=[],
        start=START,
        end=END,
        period_label="March window",
        all_time=False,
    )
    assert result.error is not None
    assert result.tables == []


async def test_all_time_context_mode(session: AsyncSession) -> None:
    _org, _repo_a, _repo_b, persons = await _seed(session)
    result = await research.run_block(
        session,
        {"op": "versus_scope", "person": persons[0].id},
        orgs=[],
        start=START,
        end=END,
        period_label="All time",
        all_time=True,
    )
    assert result.context["period_mode"] == "all time"
