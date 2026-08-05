"""Person merge and unmerge operations."""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.identity.resolver import normalize_text
from gca.metrics.rollup import recompute_for_persons
from gca.models import (
    Identity,
    MergeSuggestion,
    Person,
    PersonFilter,
    PersonRepoDayStats,
)


class MergeError(RuntimeError):
    pass


async def merge_persons(
    session: AsyncSession, target_id: int, source_id: int
) -> Person:
    """Move every identity of `source` onto `target` and delete `source`."""
    if target_id == source_id:
        raise MergeError("cannot merge a person into itself")
    target = await session.get(Person, target_id)
    source = await session.get(Person, source_id)
    if target is None or source is None:
        raise MergeError("both persons must exist")

    await session.execute(
        sa.update(Identity)
        .where(Identity.person_id == source_id)
        .values(person_id=target_id)
    )

    # Person filters: drop rows that would collide, then retarget the rest.
    target_orgs = sa.select(PersonFilter.org_id).where(
        PersonFilter.person_id == target_id
    )
    await session.execute(
        sa.delete(PersonFilter).where(
            PersonFilter.person_id == source_id,
            PersonFilter.org_id.in_(target_orgs),
        )
    )
    await session.execute(
        sa.update(PersonFilter)
        .where(PersonFilter.person_id == source_id)
        .values(person_id=target_id)
    )

    # Suggestions referencing either party are stale; the generator recreates
    # anything still relevant.
    await session.execute(
        sa.delete(MergeSuggestion).where(
            sa.or_(
                MergeSuggestion.person_a_id.in_([target_id, source_id]),
                MergeSuggestion.person_b_id.in_([target_id, source_id]),
            )
        )
    )

    await session.execute(
        sa.delete(PersonRepoDayStats).where(
            PersonRepoDayStats.person_id.in_([target_id, source_id])
        )
    )
    await session.delete(source)
    await session.flush()
    await recompute_for_persons(session, [target_id])
    return target


async def unmerge_identity(session: AsyncSession, identity_id: int) -> Person:
    """Split an identity out of its person into a fresh person."""
    identity = await session.get(Identity, identity_id)
    if identity is None:
        raise MergeError("identity does not exist")
    old_person_id = identity.person_id
    sibling_count = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(Identity)
            .where(Identity.person_id == old_person_id)
        )
    ).scalar_one()
    if sibling_count <= 1:
        raise MergeError("identity is already alone on its person")

    display = identity.name or identity.login or identity.email or "unknown"
    person = Person(display_name=display)
    session.add(person)
    await session.flush()
    identity.person_id = person.id
    await session.flush()
    await recompute_for_persons(session, [old_person_id, person.id])
    return person


async def rename_person(
    session: AsyncSession, person_id: int, display_name: str
) -> None:
    person = await session.get(Person, person_id)
    if person is None:
        raise MergeError("person does not exist")
    person.display_name = display_name.strip() or person.display_name
    await session.flush()


def person_matches(name: str, query: str) -> bool:
    return normalize_text(query) in normalize_text(name)
