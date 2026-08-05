from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.models import (
    Identity,
    IdentityKind,
    Org,
    Person,
    PersonRepoDayStats,
    Repo,
)
from gca.services import membership, stats


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _person(
    session: AsyncSession,
    name: str,
    *,
    login: str | None = None,
    email: str | None = None,
) -> Person:
    person = Person(display_name=name)
    session.add(person)
    await session.flush()
    if login:
        session.add(
            Identity(
                person_id=person.id,
                kind=IdentityKind.GITHUB_LOGIN,
                login=login,
                login_norm=login.lower(),
            )
        )
    if email:
        session.add(
            Identity(
                person_id=person.id,
                kind=IdentityKind.GIT_AUTHOR,
                name=name,
                email=email,
                name_norm=name.lower(),
                email_norm=email.lower(),
            )
        )
    await session.flush()
    return person


async def test_store_members_replaces_and_dedupes(session: AsyncSession) -> None:
    org = Org(login="acme")
    session.add(org)
    await session.flush()
    stored = await membership.store_members(
        session, org.id, [("Jane", "N1"), ("jane", "N1"), ("Bob", "")]
    )
    assert stored == 2
    stored = await membership.store_members(session, org.id, [("Jane", "N1")])
    assert stored == 1


async def test_member_person_sets_links_login_and_noreply(
    session: AsyncSession,
) -> None:
    org = Org(login="acme")
    session.add(org)
    await session.flush()
    by_login = await _person(session, "Jane", login="janedoe")
    by_noreply = await _person(
        session, "Jane D", email="123+janedoe@users.noreply.github.com"
    )
    outsider = await _person(session, "Random", email="random@fork.org")
    await membership.store_members(session, org.id, [("janedoe", "N1")])
    sets = await membership.member_person_sets(session, [org.id])
    assert by_login.id in sets[org.id]
    assert by_noreply.id in sets[org.id]
    assert outsider.id not in sets[org.id]


async def _activity(
    session: AsyncSession, person: Person, repo: Repo, org: Org
) -> None:
    session.add(
        PersonRepoDayStats(
            person_id=person.id,
            repo_id=repo.id,
            org_id=org.id,
            day=date(2026, 3, 10),
            commits=1,
            significance=1.0,
        )
    )
    await session.flush()


async def test_visible_person_ids_none_without_restrictions(
    session: AsyncSession,
) -> None:
    org = Org(login="acme")
    session.add(org)
    await session.flush()
    session.add(Repo(org_id=org.id, name="core"))
    await session.flush()
    assert await membership.visible_person_ids(session) is None


async def test_visible_person_ids_hides_fork_only_people(
    session: AsyncSession,
) -> None:
    org = Org(login="acme", ignore_forks=True)
    session.add(org)
    await session.flush()
    core = Repo(org_id=org.id, name="core")
    fork = Repo(org_id=org.id, name="fork", is_fork=True, included=False)
    session.add_all([core, fork])
    insider = await _person(session, "Ins", email="ins@acme.com")
    fork_author = await _person(session, "Upstream", email="up@elsewhere.org")
    await session.flush()
    await _activity(session, insider, core, org)
    await _activity(session, fork_author, fork, org)
    visible = await membership.visible_person_ids(session)
    assert visible == {insider.id}


async def test_visible_person_ids_members_only(session: AsyncSession) -> None:
    org = Org(login="acme", members_only=True)
    session.add(org)
    await session.flush()
    core = Repo(org_id=org.id, name="core")
    session.add(core)
    member = await _person(session, "Jane", login="janedoe")
    outsider = await _person(session, "Ext", email="ext@other.org")
    await session.flush()
    await _activity(session, member, core, org)
    await _activity(session, outsider, core, org)
    await membership.store_members(session, org.id, [("janedoe", "N1")])
    visible = await membership.visible_person_ids(session)
    assert visible == {member.id}


async def test_stats_hard_ignore_external_people(session: AsyncSession) -> None:
    """members_only removes external people from every aggregate."""
    org = Org(login="acme", members_only=True)
    session.add(org)
    await session.flush()
    core = Repo(org_id=org.id, name="core")
    session.add(core)
    member = await _person(session, "Jane", login="janedoe")
    outsider = await _person(session, "Ext", email="ext@other.org")
    await session.flush()
    await _activity(session, member, core, org)
    await _activity(session, outsider, core, org)
    await membership.store_members(session, org.id, [("janedoe", "N1")])

    board = await stats.person_leaderboard(
        session, orgs=[], start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    assert [s.person_id for s in board] == [member.id]
    totals = await stats.totals(
        session, orgs=[], start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    assert totals.commits == 1
    assert totals.active_people == 1
    repos = await stats.repo_leaderboard(
        session, orgs=[], start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    assert repos[0].contributors == 1
