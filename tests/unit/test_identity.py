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
    a = await get_or_create_git_identity(
        session, name="Jane Doe", email="jane.doe@a.com"
    )
    b = await get_or_create_git_identity(
        session, name="Doe Jane", email="jane.doe@b.io"
    )
    created, _, merged = await suggest.generate(session)
    assert created >= 1
    assert merged == 0  # heuristic evidence stays a suggestion
    for row in (await session.execute(suggest.sa.select(MergeSuggestion))).scalars():
        row.status = "dismissed"
    await session.flush()
    assert await suggest.generate(session) == (0, 0, 0)
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


async def test_merge_regenerates_suggestions_for_survivor(
    session: AsyncSession,
) -> None:
    """After a merge deletes suggestions touching the pair, still-relevant
    pairs for the surviving person must reappear immediately."""
    a = await get_or_create_git_identity(
        session, name="Jane Doe", email="jane.doe@a.com"
    )
    b = await get_or_create_git_identity(
        session, name="Doe Jane", email="jane.doe@b.io"
    )
    c = await get_or_create_git_identity(
        session, name="Jane M Doe", email="jane.doe@c.net"
    )
    created, _, merged = await suggest.generate(session)
    assert created == 3  # heuristic evidence pairs everyone
    assert merged == 0

    target = await merge_persons(session, a.person_id, b.person_id)
    pending = (
        (
            await session.execute(
                suggest.sa.select(MergeSuggestion).where(
                    MergeSuggestion.status == "pending"
                )
            )
        )
        .scalars()
        .all()
    )
    pairs = {(s.person_a_id, s.person_b_id) for s in pending}
    low, high = sorted((target.id, c.person_id))
    assert (low, high) in pairs


async def test_shared_machine_name_never_pairs_carriers(
    session: AsyncSession,
) -> None:
    """A name carried by more than two persons (a bot author or a shared
    service account absorbed into several real people) must not generate
    suggestions between the carriers: those accounts are independent."""
    await get_or_create_git_identity(session, name="DevBot", email="eva@corp.com")
    await get_or_create_git_identity(session, name="DevBot", email="juan@corp.com")
    await get_or_create_git_identity(session, name="DevBot", email="ana@corp.com")
    await get_or_create_github_identity(session, login="devbot")
    assert await suggest.generate(session) == (0, 0, 0)


async def test_login_matching_name_auto_merges(session: AsyncSession) -> None:
    """A login equal to the other person's full name with spaces removed is
    an identity proof: the scan merges instead of suggesting."""
    await get_or_create_git_identity(
        session, name="Juan Labandeira", email="jl@corp.com"
    )
    await get_or_create_github_identity(session, login="JuanLabandeira")
    created, _, merged = await suggest.generate(session)
    assert created == 0
    assert merged == 1
    persons = (await session.execute(suggest.sa.select(Person))).scalars().all()
    assert len(persons) == 1


async def test_identical_full_name_auto_merges(session: AsyncSession) -> None:
    """The same multi-token full name on two persons is an identity proof."""
    await get_or_create_git_identity(
        session, name="Adriana Hurtado", email="ah@corp.com"
    )
    await get_or_create_git_identity(
        session, name="Adriana Hurtado", email="adriana@gmail.com"
    )
    created, _, merged = await suggest.generate(session)
    assert created == 0
    assert merged == 1


async def test_identical_single_token_name_is_not_a_proof(
    session: AsyncSession,
) -> None:
    """Single-token names (handles, machine users) never auto-merge on name
    equality alone."""
    a = await get_or_create_git_identity(session, name="root", email="x@a.com")
    b = await get_or_create_git_identity(session, name="root", email="y@b.com")
    _, _, merged = await suggest.generate(session)
    assert merged == 0
    assert a.person_id != b.person_id


async def test_noreply_matching_name_auto_merges(session: AsyncSession) -> None:
    """A noreply email embedding a login that equals the other person's git
    name is an identity proof even when no login identity exists."""
    await get_or_create_git_identity(session, name="mdynamics-web", email="mw@corp.com")
    await get_or_create_git_identity(
        session,
        name="Moha Derfoufi",
        email="164+mdynamics-web@users.noreply.github.com",
    )
    created, _, merged = await suggest.generate(session)
    assert created == 0
    assert merged == 1


async def test_scan_sees_members_only_hidden_persons(session: AsyncSession) -> None:
    """The regression behind lost attribution: a git-email person hidden by
    members_only must still be scored and merged into their member person,
    otherwise their commits stay stranded on an invisible duplicate."""
    from datetime import date

    from gca.models import Org, PersonRepoDayStats, Repo
    from gca.services import membership

    org = Org(login="acme", members_only=True)
    session.add(org)
    await session.flush()
    repo = Repo(org_id=org.id, name="core")
    session.add(repo)
    await session.flush()
    member = await get_or_create_github_identity(session, login="janedoe")
    member_git = await get_or_create_git_identity(
        session, name="Jane Doe", email="jane@corp.com"
    )
    await session.execute(
        suggest.sa.update(Identity)
        .where(Identity.id == member_git.id)
        .values(person_id=member.person_id)
    )
    hidden = await get_or_create_git_identity(
        session, name="Jane X", email="jane@corp.com"
    )
    session.add(
        PersonRepoDayStats(
            person_id=hidden.person_id,
            repo_id=repo.id,
            org_id=org.id,
            day=date(2026, 3, 10),
            commits=7,
            significance=5.0,
        )
    )
    await session.flush()
    await membership.store_members(session, org.id, [("janedoe", "N1")])
    visible = await membership.visible_person_ids(session)
    assert visible is not None and hidden.person_id not in visible

    _, _, merged = await suggest.generate(session)
    assert merged == 1
    refreshed = await session.get(Identity, hidden.id)
    assert refreshed is not None and refreshed.person_id == member.person_id


async def test_generate_removes_stale_pending(session: AsyncSession) -> None:
    alice = await get_or_create_git_identity(session, name="Alice", email="a@x.com")
    bob = await get_or_create_git_identity(session, name="Bob", email="b@y.com")
    session.add(
        MergeSuggestion(
            person_a_id=min(alice.person_id, bob.person_id),
            person_b_id=max(alice.person_id, bob.person_id),
            score=0.9,
            reasons=["stale evidence"],
        )
    )
    await session.flush()
    created, removed, merged = await suggest.generate(session)
    assert created == 0
    assert removed == 1
    assert merged == 0
    remaining = (
        (await session.execute(suggest.sa.select(MergeSuggestion))).scalars().all()
    )
    assert remaining == []


async def test_generate_for_person_skips_existing_and_unrelated(
    session: AsyncSession,
) -> None:
    a = await get_or_create_git_identity(session, name="Jane", email="jane@x.com")
    await get_or_create_git_identity(session, name="J Doe", email="jane@x.com")
    await get_or_create_git_identity(session, name="Bob", email="bob@y.com")
    created = await suggest.generate_for_person(session, a.person_id)
    assert created == 1  # only the shared-email pair, Bob does not score
    assert await suggest.generate_for_person(session, a.person_id) == 0


async def test_merge_guards(session: AsyncSession) -> None:
    a = await get_or_create_git_identity(session, name="Solo", email="solo@x.com")
    with pytest.raises(MergeError):
        await merge_persons(session, a.person_id, a.person_id)
    with pytest.raises(MergeError):
        await unmerge_identity(session, a.id)


async def test_auto_merge_on_noreply_login_proof(session: AsyncSession) -> None:
    """A noreply email embedding a login is an identity proof: the scan
    merges the pair instead of suggesting it, keeping the login holder."""
    git = await get_or_create_git_identity(
        session, name="Mario", email="99+mariogzb@users.noreply.github.com"
    )
    gh = await get_or_create_github_identity(session, login="mariogzb")
    created, removed, merged = await suggest.generate(session)
    assert (created, removed, merged) == (0, 0, 1)
    persons = (await session.execute(suggest.sa.select(Person))).scalars().all()
    assert len(persons) == 1
    survivor = persons[0]
    identities = (
        (
            await session.execute(
                suggest.sa.select(Identity).where(Identity.person_id == survivor.id)
            )
        )
        .scalars()
        .all()
    )
    assert {i.id for i in identities} == {git.id, gh.id}


async def test_auto_merge_on_shared_email_proof(session: AsyncSession) -> None:
    await get_or_create_git_identity(session, name="Jane", email="jane@corp.com")
    await get_or_create_git_identity(session, name="J. Doe", email="jane@corp.com")
    created, _, merged = await suggest.generate(session)
    assert created == 0
    assert merged == 1
    persons = (await session.execute(suggest.sa.select(Person))).scalars().all()
    assert len(persons) == 1


async def test_auto_merge_adopts_full_name_over_handle(
    session: AsyncSession,
) -> None:
    """When the surviving login holder shows a handle and the absorbed person
    carried a human full name, the merged person takes the full name."""
    await get_or_create_github_identity(session, login="mariogzb")
    await get_or_create_git_identity(
        session,
        name="Mario Gonzalez Besada",
        email="9+mariogzb@users.noreply.github.com",
    )
    _, _, merged = await suggest.generate(session)
    assert merged == 1
    person = (await session.execute(suggest.sa.select(Person))).scalar_one()
    assert person.display_name == "Mario Gonzalez Besada"


async def test_auto_merge_follows_chains(session: AsyncSession) -> None:
    """Three persons proven identical pairwise collapse into one survivor."""
    await get_or_create_git_identity(session, name="A", email="jane@corp.com")
    await get_or_create_git_identity(session, name="B", email="jane@corp.com")
    await get_or_create_git_identity(session, name="C", email="jane@corp.com")
    _, _, merged = await suggest.generate(session)
    assert merged == 2
    persons = (await session.execute(suggest.sa.select(Person))).scalars().all()
    assert len(persons) == 1


async def test_dismissed_pair_is_never_auto_merged(session: AsyncSession) -> None:
    a = await get_or_create_git_identity(session, name="Jane", email="jane@corp.com")
    b = await get_or_create_git_identity(session, name="J. Doe", email="jane@corp.com")
    session.add(
        MergeSuggestion(
            person_a_id=min(a.person_id, b.person_id),
            person_b_id=max(a.person_id, b.person_id),
            score=0.99,
            reasons=["shared email jane@corp.com"],
            status="dismissed",
        )
    )
    await session.flush()
    assert await suggest.generate(session) == (0, 0, 0)
    assert a.person_id != b.person_id


async def test_pending_certain_pair_merges_on_next_scan(
    session: AsyncSession,
) -> None:
    """Certain pairs that already sit in the queue (created before the
    auto-merge feature) are merged by the next scan."""
    a = await get_or_create_git_identity(session, name="Jane", email="jane@corp.com")
    b = await get_or_create_git_identity(session, name="J. Doe", email="jane@corp.com")
    session.add(
        MergeSuggestion(
            person_a_id=min(a.person_id, b.person_id),
            person_b_id=max(a.person_id, b.person_id),
            score=0.99,
            reasons=["shared email jane@corp.com"],
        )
    )
    await session.flush()
    _, removed, merged = await suggest.generate(session)
    assert merged == 1
    assert removed == 0
    suggestions = (
        (await session.execute(suggest.sa.select(MergeSuggestion))).scalars().all()
    )
    assert suggestions == []


def test_choose_survivor_priorities() -> None:
    login_holder = PersonView(id=5, display_name="mariogzb", logins={"mariogzb"})
    git_only = PersonView(id=2, display_name="Mario Gonzalez", emails={"m@x.com"})
    target, source = suggest._choose_survivor(git_only, login_holder)
    assert target is login_holder  # a GitHub account outranks a git-only person

    named = PersonView(id=9, display_name="Mario Gonzalez", emails={"m@x.com"})
    handle = PersonView(id=3, display_name="mgonzalez", emails={"n@x.com"})
    target, source = suggest._choose_survivor(handle, named)
    assert target is named  # a human full name outranks a handle

    older = PersonView(id=1, display_name="alpha", emails={"a@x.com"})
    newer = PersonView(id=8, display_name="beta", emails={"b@x.com"})
    target, source = suggest._choose_survivor(newer, older)
    assert target is older  # ties go to the older person
