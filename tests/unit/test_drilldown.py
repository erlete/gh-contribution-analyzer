"""Drill-down rows: raw commits and PRs behind a person times repo pair."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.models import Commit, Identity, IdentityKind, Org, Person, PullRequest, Repo
from gca.services import drilldown


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[Person, Person, Repo]:
    org = Org(login="acme")
    session.add(org)
    await session.flush()
    repo = Repo(org_id=org.id, name="core")
    jane, other = Person(display_name="Jane"), Person(display_name="Other")
    session.add_all([repo, jane, other])
    await session.flush()
    ident = Identity(person_id=jane.id, kind=IdentityKind.GITHUB_LOGIN, login="jane")
    ident2 = Identity(
        person_id=other.id, kind=IdentityKind.GITHUB_LOGIN, login="other"
    )
    session.add_all([ident, ident2])
    await session.flush()
    for index in range(3):
        session.add(
            Commit(
                repo_id=repo.id,
                oid=f"deadbeef{index:032x}",
                authored_at=datetime(2026, 3, 10 + index, 12, 0, tzinfo=UTC),
                committed_at=datetime(2026, 3, 10 + index, 12, 0, tzinfo=UTC),
                author_identity_id=ident.id,
                message_subject=f"change {index}",
                additions=10 * (index + 1),
                deletions=index,
                significance=2.5,
            )
        )
    # Outside the window and by somebody else: both must never appear.
    session.add(
        Commit(
            repo_id=repo.id,
            oid="f" * 40,
            authored_at=datetime(2026, 5, 1, tzinfo=UTC),
            committed_at=datetime(2026, 5, 1, tzinfo=UTC),
            author_identity_id=ident.id,
            message_subject="late",
        )
    )
    session.add(
        Commit(
            repo_id=repo.id,
            oid="e" * 40,
            authored_at=datetime(2026, 3, 12, tzinfo=UTC),
            committed_at=datetime(2026, 3, 12, tzinfo=UTC),
            author_identity_id=ident2.id,
            message_subject="not jane",
        )
    )
    session.add(
        PullRequest(
            repo_id=repo.id,
            number=7,
            author_identity_id=ident.id,
            title="Add feature",
            state="closed",
            created_at_gh=datetime(2026, 3, 11, tzinfo=UTC),
            merged_at=datetime(2026, 3, 12, tzinfo=UTC),
        )
    )
    await session.flush()
    return jane, other, repo


async def test_commit_rows_filter_pair_and_window(session: AsyncSession) -> None:
    jane, _other, repo = await _seed(session)
    rows, total = await drilldown.commit_rows(
        session,
        person_id=jane.id,
        repo_id=repo.id,
        start=date(2026, 3, 1),
        end=date(2026, 4, 1),
        limit=2,
    )
    assert total == 3
    assert len(rows) == 2
    # Newest first, day-first dates, GitHub links.
    assert rows[0].subject == "change 2"
    assert rows[0].when == "12/03/2026"
    assert rows[0].url.startswith("https://github.com/acme/core/commit/deadbeef")


async def test_pr_rows_state_and_link(session: AsyncSession) -> None:
    jane, _other, repo = await _seed(session)
    rows, total = await drilldown.pr_rows(
        session,
        person_id=jane.id,
        repo_id=repo.id,
        start=date(2026, 3, 1),
        end=date(2026, 4, 1),
        limit=10,
    )
    assert total == 1
    assert rows[0].state == "merged"
    assert rows[0].merged == "12/03/2026"
    assert rows[0].url == "https://github.com/acme/core/pull/7"
