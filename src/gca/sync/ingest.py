"""Commit ingestion: raw git history into commits, files, churn and rollups."""

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.identity.resolver import get_or_create_git_identity
from gca.metrics.churn import DEFAULT_CHURN_WINDOW_DAYS, churn_for_file
from gca.metrics.classify import classify_path
from gca.metrics.rollup import RollupDelta, apply_deltas, day_of
from gca.metrics.significance import score_commit
from gca.models import Commit, CommitFile, Identity, Repo
from gca.sync.gitrepo import RawCommit

_BATCH = 500


@dataclass
class IngestStats:
    new_commits: int = 0
    new_files: int = 0
    churn_lines: int = 0


async def _known_oids(session: AsyncSession, repo_id: int, oids: list[str]) -> set[str]:
    known: set[str] = set()
    for start in range(0, len(oids), _BATCH):
        chunk = oids[start : start + _BATCH]
        rows = await session.execute(
            sa.select(Commit.oid).where(
                Commit.repo_id == repo_id, Commit.oid.in_(chunk)
            )
        )
        known.update(row.oid for row in rows)
    return known


async def ingest_repo(
    session: AsyncSession,
    repo: Repo,
    raw_commits: list[RawCommit],
    *,
    churn_window_days: int = DEFAULT_CHURN_WINDOW_DAYS,
) -> IngestStats:
    """Insert new commits (oldest first), compute churn, update rollups."""
    stats = IngestStats()
    if not raw_commits:
        return stats
    known = await _known_oids(session, repo.id, [c.oid for c in raw_commits])
    identity_cache: dict[tuple[str, str], Identity] = {}
    deltas: list[RollupDelta] = []
    last_oid: str | None = None

    for raw in raw_commits:
        last_oid = raw.oid
        if raw.oid in known:
            continue
        cache_key = (raw.author_name, raw.author_email)
        identity = identity_cache.get(cache_key)
        if identity is None:
            identity = await get_or_create_git_identity(
                session, name=raw.author_name, email=raw.author_email
            )
            identity_cache[cache_key] = identity

        significance, mechanical = score_commit(raw.files, is_merge=raw.is_merge)
        additions = sum(f.additions for f in raw.files)
        deletions = sum(f.deletions for f in raw.files)
        commit = Commit(
            repo_id=repo.id,
            oid=raw.oid,
            authored_at=raw.authored_at,
            committed_at=raw.committed_at,
            author_identity_id=identity.id,
            message_subject=raw.subject,
            additions=additions,
            deletions=deletions,
            files_changed=len(raw.files),
            is_merge=raw.is_merge,
            is_mechanical=mechanical,
            significance=significance,
        )
        session.add(commit)
        await session.flush()

        churn_total = self_total = cross_total = 0
        for f in raw.files:
            session.add(
                CommitFile(
                    commit_id=commit.id,
                    repo_id=repo.id,
                    authored_at=raw.authored_at,
                    path=f.path,
                    old_path=f.old_path,
                    additions=f.additions,
                    deletions=f.deletions,
                    file_class=classify_path(f.path),
                )
            )
            stats.new_files += 1
        await session.flush()

        if not raw.is_merge:
            for f in raw.files:
                if f.deletions <= 0:
                    continue
                result = await churn_for_file(
                    session,
                    repo_id=repo.id,
                    path=f.path,
                    old_path=f.old_path,
                    deletions=f.deletions,
                    authored_at=raw.authored_at,
                    author_person_id=identity.person_id,
                    window_days=churn_window_days,
                )
                churn_total += result.churn
                self_total += result.self_churn
                cross_total += result.cross_churn
        commit.churn_lines = churn_total
        commit.self_churn_lines = self_total
        commit.cross_churn_lines = cross_total

        deltas.append(
            RollupDelta(
                person_id=identity.person_id,
                repo_id=repo.id,
                org_id=repo.org_id,
                day=day_of(raw.authored_at),
                commits=1,
                additions=additions,
                deletions=deletions,
                churn=churn_total,
                self_churn=self_total,
                cross_churn=cross_total,
                significance=significance,
            )
        )
        stats.new_commits += 1
        stats.churn_lines += churn_total

    await apply_deltas(session, deltas)
    if last_oid is not None:
        repo.last_ingested_oid = last_oid
    await session.flush()
    return stats
