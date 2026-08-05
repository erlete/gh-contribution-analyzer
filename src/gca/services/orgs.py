"""Org management: add with validation, revalidate, filters."""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from gca.crypto import decrypt_str, encrypt_str
from gca.models import Org, OrgCredential
from gca.sync.api import GitHubClient
from gca.timeutil import utcnow


class OrgExistsError(RuntimeError):
    pass


async def add_org(
    session: AsyncSession,
    login: str,
    token: str,
    *,
    client_factory: type[GitHubClient] | None = None,
) -> Org:
    """Validate the token against the org and persist both. Raises on failure."""
    login = login.strip()
    existing = (
        await session.execute(sa.select(Org).where(Org.login.ilike(login)))
    ).scalar_one_or_none()
    if existing is not None:
        raise OrgExistsError(f"organization {login} is already configured")
    factory = client_factory or GitHubClient
    async with factory(token.strip()) as client:
        info = await client.validate_org(login)
        org = Org(
            login=info.login,
            display_name=info.name,
            avatar_url=info.avatar_url or None,
        )
        session.add(org)
        await session.flush()
        session.add(
            OrgCredential(
                org_id=org.id,
                token_encrypted=encrypt_str(token.strip()),
                validated_at=utcnow(),
                rate_snapshot=dict(client.rate_snapshot),
            )
        )
        await session.flush()
    return org


async def update_token(
    session: AsyncSession,
    org_id: int,
    token: str,
    *,
    client_factory: type[GitHubClient] | None = None,
) -> None:
    org = await session.get_one(Org, org_id, options=[selectinload(Org.credential)])
    factory = client_factory or GitHubClient
    async with factory(token.strip()) as client:
        await client.validate_org(org.login)
        if org.credential is None:
            session.add(
                OrgCredential(
                    org_id=org.id,
                    token_encrypted=encrypt_str(token.strip()),
                    validated_at=utcnow(),
                )
            )
        else:
            org.credential.token_encrypted = encrypt_str(token.strip())
            org.credential.validated_at = utcnow()
            org.credential.rate_snapshot = dict(client.rate_snapshot)
    org.sync_status = "idle"
    org.sync_error = None
    await session.flush()


async def revalidate(
    session: AsyncSession,
    org_id: int,
    *,
    client_factory: type[GitHubClient] | None = None,
) -> bool:
    org = await session.get_one(Org, org_id, options=[selectinload(Org.credential)])
    if org.credential is None:
        return False
    token = decrypt_str(org.credential.token_encrypted)
    factory = client_factory or GitHubClient
    try:
        async with factory(token) as client:
            await client.validate_org(org.login)
            org.credential.validated_at = utcnow()
            org.credential.rate_snapshot = dict(client.rate_snapshot)
        org.sync_error = None
        if org.sync_status == "degraded":
            org.sync_status = "idle"
        await session.flush()
        return True
    except Exception as exc:
        org.sync_status = "degraded"
        org.sync_error = str(exc)[:500]
        await session.flush()
        return False


async def has_validated_org(session: AsyncSession) -> bool:
    count = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(OrgCredential)
            .where(OrgCredential.validated_at.is_not(None))
        )
    ).scalar_one()
    return count > 0
