"""Identity lookup and creation.

Every unknown identity auto-creates its own person; merges only ever move the
`person_id` foreign key, so identities stay immutable and unmerge is always
possible. Creation is raced by concurrent repo syncs, so inserts run inside a
savepoint and fall back to re-selecting on unique-index conflicts.
`commit_on_create` lets long-running sync transactions release the unique
index locks immediately, keeping concurrent workers from blocking on each
other.
"""

import unicodedata

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gca.models import Identity, IdentityKind, Person


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value.strip().casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.split())


async def _select_git_identity(
    session: AsyncSession, name_norm: str, email_norm: str
) -> Identity | None:
    return (
        await session.execute(
            sa.select(Identity).where(
                Identity.kind == IdentityKind.GIT_AUTHOR,
                Identity.name_norm == name_norm,
                Identity.email_norm == email_norm,
            )
        )
    ).scalar_one_or_none()


async def get_or_create_git_identity(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    commit_on_create: bool = False,
) -> Identity:
    name_norm = normalize_text(name)
    email_norm = normalize_text(email)
    existing = await _select_git_identity(session, name_norm, email_norm)
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            person = Person(display_name=name.strip() or email.strip() or "unknown")
            session.add(person)
            await session.flush()
            identity = Identity(
                person_id=person.id,
                kind=IdentityKind.GIT_AUTHOR,
                name=name,
                email=email,
                name_norm=name_norm,
                email_norm=email_norm,
            )
            session.add(identity)
            await session.flush()
    except IntegrityError:
        raced = await _select_git_identity(session, name_norm, email_norm)
        if raced is not None:
            return raced
        raise
    if commit_on_create:
        await session.commit()
    return identity


async def _select_github_identity(
    session: AsyncSession, login_norm: str, node_id: str | None
) -> Identity | None:
    if node_id:
        by_node = (
            await session.execute(
                sa.select(Identity).where(
                    Identity.kind == IdentityKind.GITHUB_LOGIN,
                    Identity.node_id == node_id,
                )
            )
        ).scalar_one_or_none()
        if by_node is not None:
            return by_node
    return (
        await session.execute(
            sa.select(Identity).where(
                Identity.kind == IdentityKind.GITHUB_LOGIN,
                Identity.login_norm == login_norm,
            )
        )
    ).scalar_one_or_none()


async def get_or_create_github_identity(
    session: AsyncSession,
    *,
    login: str,
    node_id: str | None = None,
    avatar_url: str | None = None,
    commit_on_create: bool = False,
) -> Identity:
    login_norm = normalize_text(login)
    existing = await _select_github_identity(session, login_norm, node_id)
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            person = Person(display_name=login)
            session.add(person)
            await session.flush()
            identity = Identity(
                person_id=person.id,
                kind=IdentityKind.GITHUB_LOGIN,
                login=login,
                login_norm=login_norm,
                node_id=node_id,
                avatar_url=avatar_url,
            )
            session.add(identity)
            await session.flush()
    except IntegrityError:
        raced = await _select_github_identity(session, login_norm, node_id)
        if raced is not None:
            return raced
        raise
    if commit_on_create:
        await session.commit()
    return identity
