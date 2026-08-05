"""Commit attribution reconciliation: GitHub's email-to-account mapping as
an identity proof that merges heuristically unlinkable duplicates."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.identity.attribution import (
    ATTRIBUTION_CACHE_KEY,
    reconcile_commit_attribution,
)
from gca.identity.resolver import (
    get_or_create_git_identity,
    get_or_create_github_identity,
)
from gca.models import Commit, Identity, MergeSuggestion, Org, Person, Repo
from gca.services.settings import SettingsStore


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


class FakeResolver:
    """Maps email keys to (login, node_id); records every batch it gets."""

    def __init__(self, mapping: dict[str, tuple[str, str]]) -> None:
        self.mapping = mapping
        self.calls: list[list[tuple[str, str, str]]] = []

    async def commit_authors(
        self, owner: str, lookups: list[tuple[str, str, str]]
    ) -> dict[str, tuple[str, str]]:
        self.calls.append(lookups)
        return {key: self.mapping[key] for key, _, _ in lookups if key in self.mapping}


async def _org_repo(session: AsyncSession) -> tuple[Org, Repo]:
    org = Org(login="acme")
    session.add(org)
    await session.flush()
    repo = Repo(org_id=org.id, name="core")
    session.add(repo)
    await session.flush()
    return org, repo


async def _commit(
    session: AsyncSession, repo: Repo, identity_id: int, oid: str
) -> None:
    session.add(
        Commit(
            repo_id=repo.id,
            oid=oid,
            authored_at=datetime(2026, 3, 10, tzinfo=UTC),
            committed_at=datetime(2026, 3, 10, tzinfo=UTC),
            author_identity_id=identity_id,
        )
    )
    await session.flush()


async def test_merges_into_existing_account_person(session: AsyncSession) -> None:
    org, repo = await _org_repo(session)
    account = await get_or_create_github_identity(session, login="erlete-dlt")
    git = await get_or_create_git_identity(
        session, name="Paulo Sánchez", email="psanchez@corp.com"
    )
    await _commit(session, repo, git.id, "a" * 40)
    resolver = FakeResolver({"psanchez@corp.com": ("erlete-dlt", "U_1")})

    merged = await reconcile_commit_attribution(
        session, resolver, org_id=org.id, org_login="acme"
    )
    assert merged == 1
    refreshed = await session.get(Identity, git.id)
    assert refreshed is not None
    assert refreshed.person_id == account.person_id
    survivor = await session.get_one(Person, account.person_id)
    # The account handle gives way to the human full name.
    assert survivor.display_name == "Paulo Sánchez"


async def test_creates_account_identity_when_absent(session: AsyncSession) -> None:
    org, repo = await _org_repo(session)
    git = await get_or_create_git_identity(
        session, name="Carlos Alonso", email="carlos.alonso@corp.com"
    )
    await _commit(session, repo, git.id, "b" * 40)
    resolver = FakeResolver({"carlos.alonso@corp.com": ("carlosalfe", "U_2")})

    merged = await reconcile_commit_attribution(
        session, resolver, org_id=org.id, org_login="acme"
    )
    assert merged == 1
    login_identity = (
        await session.execute(sa.select(Identity).where(Identity.login == "carlosalfe"))
    ).scalar_one()
    refreshed = await session.get(Identity, git.id)
    assert refreshed is not None
    assert refreshed.person_id == login_identity.person_id
    persons = (await session.execute(sa.select(Person))).scalars().all()
    assert len(persons) == 1


async def test_unresolved_email_is_cached_and_not_reasked(
    session: AsyncSession,
) -> None:
    org, repo = await _org_repo(session)
    git = await get_or_create_git_identity(session, name="rise", email="rise@ext.org")
    await _commit(session, repo, git.id, "c" * 40)
    resolver = FakeResolver({})

    assert (
        await reconcile_commit_attribution(
            session, resolver, org_id=org.id, org_login="acme"
        )
        == 0
    )
    assert len(resolver.calls) == 1
    cache = await SettingsStore(session).get(ATTRIBUTION_CACHE_KEY) or {}
    assert "rise@ext.org" in cache

    # Within the TTL the email is not asked again; no client call happens.
    assert (
        await reconcile_commit_attribution(
            session, resolver, org_id=org.id, org_login="acme"
        )
        == 0
    )
    assert len(resolver.calls) == 1


async def test_stale_cache_entry_is_reasked(session: AsyncSession) -> None:
    org, repo = await _org_repo(session)
    git = await get_or_create_git_identity(session, name="rise", email="rise@ext.org")
    await _commit(session, repo, git.id, "d" * 40)
    stale = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    await SettingsStore(session).set(ATTRIBUTION_CACHE_KEY, {"rise@ext.org": stale})
    resolver = FakeResolver({})

    await reconcile_commit_attribution(
        session, resolver, org_id=org.id, org_login="acme"
    )
    assert len(resolver.calls) == 1


async def test_dismissed_pair_is_not_merged(session: AsyncSession) -> None:
    org, repo = await _org_repo(session)
    account = await get_or_create_github_identity(session, login="erlete-dlt")
    git = await get_or_create_git_identity(
        session, name="Paulo Sánchez", email="psanchez@corp.com"
    )
    await _commit(session, repo, git.id, "e" * 40)
    low, high = sorted((account.person_id, git.person_id))
    session.add(
        MergeSuggestion(
            person_a_id=low,
            person_b_id=high,
            score=0.7,
            reasons=["similar names (0.85)"],
            status="dismissed",
        )
    )
    await session.flush()
    resolver = FakeResolver({"psanchez@corp.com": ("erlete-dlt", "U_1")})

    merged = await reconcile_commit_attribution(
        session, resolver, org_id=org.id, org_login="acme"
    )
    assert merged == 0
    refreshed = await session.get(Identity, git.id)
    assert refreshed is not None
    assert refreshed.person_id != account.person_id


async def test_linked_persons_are_not_candidates(session: AsyncSession) -> None:
    """A person already holding a login never triggers a lookup."""
    org, repo = await _org_repo(session)
    account = await get_or_create_github_identity(session, login="janedoe")
    git = await get_or_create_git_identity(session, name="Jane", email="jane@corp.com")
    await session.execute(
        sa.update(Identity)
        .where(Identity.id == git.id)
        .values(person_id=account.person_id)
    )
    await _commit(session, repo, git.id, "f" * 40)
    resolver = FakeResolver({})

    await reconcile_commit_attribution(
        session, resolver, org_id=org.id, org_login="acme"
    )
    assert resolver.calls == []
