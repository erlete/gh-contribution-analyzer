"""Churn computation.

Churn for a commit file = min(deletions, lines added to the same path within
the trailing window). A file-level approximation computed purely from stored
numstat history: honest about not tracking individual lines, cheap to compute,
and stable across re-ingests. Renames also look back at the old path. The
self/cross split allocates churned lines proportionally to who added the
recent lines.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.models import Commit, CommitFile, Identity

DEFAULT_CHURN_WINDOW_DAYS = 21


@dataclass
class ChurnResult:
    churn: int
    self_churn: int
    cross_churn: int


async def churn_for_file(
    session: AsyncSession,
    *,
    repo_id: int,
    path: str,
    old_path: str | None,
    deletions: int,
    authored_at: datetime,
    author_person_id: int,
    window_days: int = DEFAULT_CHURN_WINDOW_DAYS,
) -> ChurnResult:
    if deletions <= 0:
        return ChurnResult(0, 0, 0)
    window_start = authored_at - timedelta(days=window_days)
    paths = {path}
    if old_path:
        paths.add(old_path)
    rows = (
        await session.execute(
            sa.select(
                Identity.person_id,
                sa.func.sum(CommitFile.additions).label("added"),
            )
            .join(Commit, CommitFile.commit_id == Commit.id)
            .join(Identity, Commit.author_identity_id == Identity.id)
            .where(
                CommitFile.repo_id == repo_id,
                CommitFile.path.in_(paths),
                Commit.authored_at >= window_start,
                Commit.authored_at < authored_at,
            )
            .group_by(Identity.person_id)
        )
    ).all()
    total_recent = sum(int(r.added or 0) for r in rows)
    if total_recent <= 0:
        return ChurnResult(0, 0, 0)
    churned = min(deletions, total_recent)
    self_recent = sum(
        int(r.added or 0) for r in rows if r.person_id == author_person_id
    )
    self_share = round(churned * (self_recent / total_recent))
    return ChurnResult(
        churn=churned, self_churn=self_share, cross_churn=churned - self_share
    )
