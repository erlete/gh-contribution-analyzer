"""Commit-to-account attribution from GitHub as an identity proof.

Heuristics cannot link a git email to a GitHub account when they share no
text ("Paulo Sánchez" <psanchez@corp.com> vs login erlete-dlt). GitHub can:
it attributes every commit whose author email is registered on an account
to that account, and the API exposes the mapping per commit. After each org
sync we take the emails still unlinked to any login, ask GitHub about ONE
known commit per email (batched into a single GraphQL request), and merge
the proven pairs through the normal merge path.

Emails GitHub cannot resolve are cached with a timestamp and re-checked
after a TTL, because people register addresses on their account later.
Dismissed suggestion pairs are never merged; a human already said no.
"""

import logging
from datetime import datetime, timedelta
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from gca.identity.merge import merge_persons
from gca.identity.resolver import get_or_create_github_identity
from gca.models import Commit, Identity, IdentityKind, MergeSuggestion, Person, Repo
from gca.services import audit
from gca.services.settings import SettingsStore
from gca.timeutil import utcnow

log = logging.getLogger("gca.identity")

ATTRIBUTION_CACHE_KEY = "commit_attribution_checked"
# Unresolved emails are re-asked after this long; owners register addresses
# on their accounts later and the next sync should pick that up.
RECHECK_DAYS = 7
# Cache entries older than this are dropped so the settings row stays small.
CACHE_RETENTION_DAYS = 60
# Upper bound of lookups per sync; the set shrinks as merges resolve it.
MAX_LOOKUPS_PER_SYNC = 50


class CommitAuthorResolver(Protocol):
    async def commit_authors(
        self, owner: str, lookups: list[tuple[str, str, str]]
    ) -> dict[str, tuple[str, str]]: ...


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def _unlinked_emails(session: AsyncSession) -> list[str]:
    """Distinct git author emails on persons that hold no GitHub login."""
    other = aliased(Identity)
    has_login = sa.select(other.id).where(
        other.person_id == Identity.person_id,
        sa.func.coalesce(other.login_norm, "") != "",
    )
    rows = await session.execute(
        sa.select(Identity.email_norm)
        .where(
            Identity.kind == IdentityKind.GIT_AUTHOR,
            sa.func.coalesce(Identity.email_norm, "") != "",
            ~has_login.exists(),
        )
        .distinct()
    )
    return sorted(row.email_norm for row in rows)


async def _pair_dismissed(session: AsyncSession, a: int, b: int) -> bool:
    low, high = sorted((a, b))
    return (
        await session.scalar(
            sa.select(MergeSuggestion.id).where(
                MergeSuggestion.person_a_id == low,
                MergeSuggestion.person_b_id == high,
                MergeSuggestion.status == "dismissed",
            )
        )
    ) is not None


async def reconcile_commit_attribution(
    session: AsyncSession,
    client: CommitAuthorResolver,
    *,
    org_id: int,
    org_login: str,
) -> int:
    """Merge unlinked git-email persons into the accounts GitHub attributes
    their commits to. Returns the number of merges performed."""
    store = SettingsStore(session)
    now = utcnow()
    cache = await store.get(ATTRIBUTION_CACHE_KEY) or {}
    recheck_cutoff = now - timedelta(days=RECHECK_DAYS)

    lookups: list[tuple[str, str, str]] = []
    for email in await _unlinked_emails(session):
        checked = _parse_ts(cache.get(email))
        if checked is not None and checked > recheck_cutoff:
            continue
        commit_row = (
            await session.execute(
                sa.select(Commit.oid, Repo.name)
                .join(Identity, Commit.author_identity_id == Identity.id)
                .join(Repo, Commit.repo_id == Repo.id)
                .where(Identity.email_norm == email, Repo.org_id == org_id)
                .order_by(Commit.authored_at.desc())
                .limit(1)
            )
        ).first()
        if commit_row is None:
            # No commit under this org; another org's sync owns the lookup.
            continue
        lookups.append((email, commit_row.name, commit_row.oid))
        if len(lookups) >= MAX_LOOKUPS_PER_SYNC:
            break
    if not lookups:
        return 0

    resolved = await client.commit_authors(org_login, lookups)

    merged = 0
    for email, login_pair in resolved.items():
        login, node_id = login_pair
        account = await get_or_create_github_identity(
            session, login=login, node_id=node_id or None
        )
        source_ids = list(
            (
                await session.execute(
                    sa.select(Identity.person_id)
                    .where(
                        Identity.kind == IdentityKind.GIT_AUTHOR,
                        Identity.email_norm == email,
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        for source_id in source_ids:
            target_id = (
                await session.execute(
                    sa.select(Identity.person_id).where(Identity.id == account.id)
                )
            ).scalar_one()
            if source_id == target_id:
                continue
            if await _pair_dismissed(session, source_id, target_id):
                log.info(
                    "attribution proof for %s -> %s skipped: pair dismissed",
                    email,
                    login,
                )
                continue
            source = await session.get_one(Person, source_id)
            source_name = source.display_name
            survivor = await merge_persons(session, target_id, source_id)
            if " " not in survivor.display_name.strip() and " " in source_name.strip():
                survivor.display_name = source_name
                await session.flush()
            await audit.record(
                session,
                kind="identity.auto_merged",
                actor="system",
                subject=survivor.display_name,
                message=(
                    f"GitHub attributes {email} to {login}; merged "
                    f"{source_name} into {survivor.display_name}"
                ),
            )
            merged += 1

    retention_cutoff = now - timedelta(days=CACHE_RETENTION_DAYS)
    kept = {
        email: ts
        for email, ts in cache.items()
        if (parsed := _parse_ts(ts)) is not None and parsed > retention_cutoff
    }
    for email, _, _ in lookups:
        if email not in resolved:
            kept[email] = now.isoformat()
        else:
            kept.pop(email, None)
    await store.set(ATTRIBUTION_CACHE_KEY, kept)
    return merged
