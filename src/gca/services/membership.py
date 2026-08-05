"""Org membership snapshots and hard visibility rules.

Two per-org hard filters exist. `ignore_forks` acts through `Repo.included`,
which every stats query already respects. `members_only` restricts persons:
a person counts as a member of an org when one of their identities is the
member's GitHub login, or a git identity committing under that login's
GitHub noreply address. `visible_person_ids` combines both rules into the
set of persons any surface (people lists, identity management, suggestions,
report selectors) is allowed to show.
"""

import re

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.identity.resolver import normalize_text
from gca.models import Identity, Org, OrgMember, PersonRepoDayStats, Repo

_NOREPLY_RE = re.compile(r"^(?:\d+\+)?([a-z0-9-]+)@users\.noreply\.github\.com$")


async def store_members(
    session: AsyncSession, org_id: int, members: list[tuple[str, str]]
) -> int:
    """Replace the membership snapshot for an org. `members` holds
    (login, node_id) pairs. Returns the stored count."""
    await session.execute(sa.delete(OrgMember).where(OrgMember.org_id == org_id))
    seen: set[str] = set()
    for login, node_id in members:
        norm = normalize_text(login)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        session.add(
            OrgMember(
                org_id=org_id,
                login=login,
                login_norm=norm,
                node_id=node_id or None,
            )
        )
    await session.flush()
    return len(seen)


async def _member_logins(
    session: AsyncSession, org_ids: list[int]
) -> dict[int, set[str]]:
    rows = (
        await session.execute(
            sa.select(OrgMember.org_id, OrgMember.login_norm).where(
                OrgMember.org_id.in_(org_ids)
            )
        )
    ).all()
    logins: dict[int, set[str]] = {org_id: set() for org_id in org_ids}
    for row in rows:
        logins[row.org_id].add(row.login_norm)
    return logins


async def member_person_sets(
    session: AsyncSession, org_ids: list[int]
) -> dict[int, set[int]]:
    """For each org, the person ids linked to a member: by GitHub login or
    by a git identity using the member's noreply address."""
    if not org_ids:
        return {}
    logins_by_org = await _member_logins(session, org_ids)
    identity_rows = (
        await session.execute(
            sa.select(Identity.person_id, Identity.login_norm, Identity.email_norm)
        )
    ).all()
    result: dict[int, set[int]] = {org_id: set() for org_id in org_ids}
    for row in identity_rows:
        login = row.login_norm
        if not login and row.email_norm:
            match = _NOREPLY_RE.match(row.email_norm)
            login = match.group(1) if match else None
        if not login:
            continue
        for org_id, logins in logins_by_org.items():
            if login in logins:
                result[org_id].add(row.person_id)
    return result


async def visible_person_ids(session: AsyncSession) -> set[int] | None:
    """Persons visible somewhere: they have activity in an included repo of
    an org whose members rule they pass. Returns None when no restriction is
    active (every repo included, no members_only org), so callers can skip
    filtering entirely."""
    orgs = (await session.execute(sa.select(Org.id, Org.members_only))).all()
    restricted = [row.id for row in orgs if row.members_only]
    any_excluded = (
        await session.scalar(
            sa.select(sa.func.count()).select_from(Repo).where(Repo.included.is_(False))
        )
    ) or 0
    if not restricted and not any_excluded:
        return None
    member_sets = await member_person_sets(session, restricted)
    activity = (
        await session.execute(
            sa.select(PersonRepoDayStats.person_id, PersonRepoDayStats.org_id)
            .join(Repo, PersonRepoDayStats.repo_id == Repo.id)
            .where(Repo.included.is_(True))
            .distinct()
        )
    ).all()
    visible: set[int] = set()
    for row in activity:
        allowed = member_sets.get(row.org_id)
        if allowed is not None and row.person_id not in allowed:
            continue
        visible.add(row.person_id)
    return visible
