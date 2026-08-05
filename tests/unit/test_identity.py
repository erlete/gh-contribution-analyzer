from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.identity import suggest
from gca.identity.merge import MergeError, merge_persons, unmerge_identity
from gca.identity.resolver import (
    get_or_create_git_identity,
    get_or_create_github_identity,
    normalize_text,
)
from gca.identity.suggest import PersonView, score_pair
from gca.models import Identity, MergeSuggestion, Person


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_normalize_text() -> None:
    assert normalize_text("  Paulo  SÁNCHEZ ") == "paulo sanchez"
    assert normalize_text(None) == ""
    assert normalize_text("Émile Zölà") == "emile zola"
    # Stroked letters have no NFKD decomposition and pass through lowercased.
    assert normalize_text("Đörte") == "đorte"


async def test_git_identity_dedupe(session: AsyncSession) -> None:
    a = await get_or_create_git_identity(session, name="Jane Doe", email="J@X.com")
    b = await get_or_create_git_identity(session, name="jane doe", email="j@x.com")
    c = await get_or_create_git_identity(session, name="Jane D.", email="j@x.com")
    assert a.id == b.id
    assert c.id != a.id
    assert a.person_id != c.person_id


async def test_github_identity_by_node_id(session: AsyncSession) -> None:
    a = await get_or_create_github_identity(session, login="olduser", node_id="N1")
    b = await get_or_create_github_identity(session, login="newuser", node_id="N1")
    assert a.id == b.id


def test_score_shared_email() -> None:
    a = PersonView(id=1, display_name="A", emails={"jane@corp.com"})
    b = PersonView(id=2, display_name="B", emails={"jane@corp.com"})
    score, reasons = score_pair(a, b)
    assert score >= 0.99
    assert any("shared email" in r for r in reasons)


def test_score_noreply_login() -> None:
    a = PersonView(
        id=1,
        display_name="A",
        emails={"1234567+janedoe@users.noreply.github.com"},
    )
    b = PersonView(id=2, display_name="B", logins={"janedoe"})
    score, reasons = score_pair(a, b)
    assert score >= 0.9
    assert any("noreply" in r for r in reasons)


def test_score_name_similarity_and_local_part() -> None:
    a = PersonView(
        id=1, display_name="A", names={"jane doe"}, emails={"jane.doe@a.com"}
    )
    b = PersonView(id=2, display_name="B", names={"doe jane"}, emails={"jane.doe@b.io"})
    score, reasons = score_pair(a, b)
    assert score >= 0.55
    assert any("local part" in r for r in reasons)


def test_score_generic_local_part_ignored() -> None:
    a = PersonView(id=1, display_name="A", emails={"info@a.com"})
    b = PersonView(id=2, display_name="B", emails={"info@b.com"})
    score, _ = score_pair(a, b)
    assert score < 0.55


def test_score_unrelated() -> None:
    a = PersonView(id=1, display_name="A", names={"alice"}, emails={"alice@a.com"})
    b = PersonView(id=2, display_name="B", names={"bob"}, emails={"bob@b.com"})
    score, _ = score_pair(a, b)
    assert score < 0.3


async def test_generate_skips_dismissed(session: AsyncSession) -> None:
    a = await get_or_create_git_identity(session, name="Jane", email="jane@x.com")
    b = await get_or_create_github_identity(session, login="janedoe")
    await get_or_create_git_identity(
        session, name="Jane Doe", email="1+janedoe@users.noreply.github.com"
    )
    created = await suggest.generate(session)
    assert created >= 1
    for row in (await session.execute(suggest.sa.select(MergeSuggestion))).scalars():
        row.status = "dismissed"
    await session.flush()
    assert await suggest.generate(session) == 0
    assert a.person_id != b.person_id


async def test_merge_and_unmerge(session: AsyncSession) -> None:
    a = await get_or_create_git_identity(session, name="Jane", email="jane@x.com")
    b = await get_or_create_github_identity(session, login="janedoe")
    target = await merge_persons(session, a.person_id, b.person_id)
    identities = (
        (
            await session.execute(
                suggest.sa.select(Identity).where(Identity.person_id == target.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(identities) == 2
    persons = (await session.execute(suggest.sa.select(Person))).scalars().all()
    assert len(persons) == 1

    fresh = await unmerge_identity(session, b.id)
    assert fresh.id != target.id
    refreshed = await session.get(Identity, b.id)
    assert refreshed is not None and refreshed.person_id == fresh.id


async def test_merge_guards(session: AsyncSession) -> None:
    a = await get_or_create_git_identity(session, name="Solo", email="solo@x.com")
    with pytest.raises(MergeError):
        await merge_persons(session, a.person_id, a.person_id)
    with pytest.raises(MergeError):
        await unmerge_identity(session, a.id)
