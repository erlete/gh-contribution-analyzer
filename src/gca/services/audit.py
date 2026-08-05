"""Operational audit trail.

Every state-changing operation records one event: syncs, report lifecycles,
mail sendings, org and policy changes, identity merges. The operations page
reads this table for current and historical activity.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gca.models import AuditEvent


async def record(
    session: AsyncSession,
    *,
    kind: str,
    message: str,
    subject: str = "",
    actor: str = "admin",
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(kind=kind, message=message, subject=subject, actor=actor, data=data)
    )
    await session.flush()
