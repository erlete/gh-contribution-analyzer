"""Org-level sync orchestration with bounded concurrency and checkpoints."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from gca.crypto import decrypt_str
from gca.metrics.rollup import recompute_for_persons
from gca.models import (
    CloneStatus,
    Commit,
    FilterMode,
    Identity,
    Org,
    Person,
    PersonRepoDayStats,
    PullRequest,
    Repo,
    RepoFilter,
    Review,
    SyncRun,
)
from gca.sync.api import (
    AuthError,
    GitHubClient,
    NotAnOrgError,
    RateLimitError,
    RepoInfo,
)
from gca.sync.gitrepo import GitError, GitMirror, RawCommit, rmtree_robust
from gca.sync.ingest import ingest_repo
from gca.sync.prsync import upsert_pull_requests
from gca.timeutil import ensure_utc

DEFAULT_REPO_URL_TEMPLATE = "https://github.com/{org}/{name}.git"
ClientFactory = Callable[[str], GitHubClient]
SessionFactory = async_sessionmaker[AsyncSession]

_TRANSIENT_GIT_MARKERS = (
    "Could not connect",
    "Failed to connect",
    "Connection timed out",
    "Could not resolve host",
    "Connection reset",
    "early EOF",
)


def _is_transient_git_error(exc: GitError) -> bool:
    text = str(exc)
    return any(marker in text for marker in _TRANSIENT_GIT_MARKERS)


@dataclass
class SyncSummary:
    org_id: int
    repos_processed: int = 0
    new_commits: int = 0
    new_prs: int = 0
    errors: list[str] = field(default_factory=list)
    degraded: bool = False
    affected_person_ids: set[int] = field(default_factory=set)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _repo_included(name: str, mode: FilterMode, filter_names: set[str]) -> bool:
    lowered = name.lower()
    names = {n.lower() for n in filter_names}
    if mode == FilterMode.WHITELIST:
        return lowered in names
    if mode == FilterMode.BLACKLIST:
        return lowered not in names
    return True


async def _discover(
    session: AsyncSession, org: Org, repo_infos: list[RepoInfo]
) -> list[int]:
    """Upsert repos, apply filters, return ids of included repos."""
    filter_names = {
        row.repo_name
        for row in (
            await session.execute(
                sa.select(RepoFilter).where(RepoFilter.org_id == org.id)
            )
        ).scalars()
    }
    rows = list(
        (await session.execute(sa.select(Repo).where(Repo.org_id == org.id))).scalars()
    )
    by_name = {repo.name.lower(): repo for repo in rows}
    by_node = {repo.node_id: repo for repo in rows if repo.node_id}
    included_ids: list[int] = []
    seen_ids: set[int] = set()
    for info in repo_infos:
        repo = by_node.get(info.node_id) if info.node_id else None
        if repo is None:
            repo = by_name.get(info.name.lower())
        if repo is None:
            repo = Repo(org_id=org.id, name=info.name)
            session.add(repo)
        elif repo.name.lower() != info.name.lower():
            # Renamed on GitHub: keep the row (history stays), restart the
            # clone under the new name. The old clone dir is orphan-collected
            # by worker maintenance.
            repo.name = info.name
            repo.clone_status = CloneStatus.PENDING
            repo.last_fetched_at = None
        repo.node_id = info.node_id or repo.node_id
        repo.default_branch = info.default_branch
        repo.is_private = info.is_private
        repo.is_archived = info.is_archived
        repo.is_fork = info.is_fork
        repo.included = _repo_included(info.name, org.repo_filter_mode, filter_names)
        await session.flush()
        seen_ids.add(repo.id)
        if repo.included:
            included_ids.append(repo.id)
    # Repos gone from GitHub (deleted or access revoked) stop syncing but keep
    # their history.
    for repo in rows:
        if repo.id not in seen_ids and repo.included:
            repo.included = False
    await session.flush()
    return included_ids


async def sync_org(
    session_factory: SessionFactory,
    org_id: int,
    *,
    clone_dir: str | Path,
    concurrency: int = 2,
    client_factory: ClientFactory = GitHubClient,
    repo_url_template: str = DEFAULT_REPO_URL_TEMPLATE,
) -> SyncSummary:
    summary = SyncSummary(org_id=org_id)

    async with session_factory() as session:
        org = await session.get(Org, org_id, options=[selectinload(Org.credential)])
        if org is None:
            summary.errors.append("org not found")
            return summary
        credential = org.credential
        if credential is None:
            org.sync_status = "degraded"
            org.sync_error = "no token configured"
            await session.commit()
            summary.degraded = True
            return summary
        token = decrypt_str(credential.token_encrypted)
        org_login = org.login
        org.sync_status = "running"
        org.sync_error = None
        await session.commit()

    client = client_factory(token)
    try:
        async with session_factory() as session:
            org = await session.get_one(
                Org, org_id, options=[selectinload(Org.credential)]
            )
            run = SyncRun(org_id=org_id, kind="discovery")
            session.add(run)
            try:
                info = await client.validate_org(org_login)
                org.display_name = info.name
                org.avatar_url = info.avatar_url or None
                if org.credential is not None:
                    org.credential.validated_at = _utcnow()
                    org.credential.rate_snapshot = dict(client.rate_snapshot)
                repo_infos = await client.list_repos(org_login)
                included_ids = await _discover(session, org, repo_infos)
                run.status = "success"
                run.stats = {
                    "repos_total": len(repo_infos),
                    "repos_included": len(included_ids),
                }
            except Exception as exc:
                run.status = "error"
                run.error = str(exc)[:2000]
                raise
            finally:
                run.finished_at = _utcnow()
                await session.commit()

        semaphore = asyncio.Semaphore(concurrency)
        stop = asyncio.Event()

        async def _one(repo_id: int) -> None:
            if stop.is_set():
                return
            async with semaphore:
                if stop.is_set():
                    return
                try:
                    await _sync_repo(
                        session_factory,
                        client,
                        org_id,
                        org_login,
                        repo_id,
                        clone_dir=clone_dir,
                        token=token,
                        repo_url_template=repo_url_template,
                        summary=summary,
                    )
                except RateLimitError, AuthError:
                    stop.set()
                    raise

        results = await asyncio.gather(
            *(_one(rid) for rid in included_ids), return_exceptions=True
        )
        fatal: RateLimitError | AuthError | None = None
        for result in results:
            if isinstance(result, RateLimitError | AuthError):
                fatal = result
            elif isinstance(result, BaseException):
                summary.errors.append(str(result)[:500])

        # PR and review rollups rebuild once, after every repo task is done,
        # so concurrent recomputes can never race each other or the ingests.
        if summary.affected_person_ids:
            async with session_factory() as session:
                await recompute_for_persons(
                    session, sorted(summary.affected_person_ids)
                )
                await session.commit()
        if fatal is not None:
            raise fatal

        async with session_factory() as session:
            org = await session.get_one(
                Org, org_id, options=[selectinload(Org.credential)]
            )
            org.sync_status = "idle"
            org.sync_error = (
                f"{len(summary.errors)} repos failed in the last sync"
                if summary.errors
                else None
            )
            org.last_synced_at = _utcnow()
            if org.credential is not None:
                org.credential.rate_snapshot = dict(client.rate_snapshot)
            await session.commit()
    except (AuthError, NotAnOrgError) as exc:
        summary.degraded = True
        summary.errors.append(str(exc))
        await _mark_degraded(session_factory, org_id, f"token problem: {exc}")
    except RateLimitError as exc:
        summary.degraded = True
        reset = exc.reset_at.isoformat() if exc.reset_at else "unknown"
        summary.errors.append(str(exc))
        await _mark_degraded(
            session_factory, org_id, f"rate limit exhausted, resets at {reset}"
        )
    except Exception as exc:
        summary.errors.append(str(exc)[:500])
        await _mark_degraded(session_factory, org_id, str(exc)[:500])
    finally:
        await client.close()
    return summary


async def _mark_degraded(
    session_factory: SessionFactory, org_id: int, message: str
) -> None:
    async with session_factory() as session:
        org = await session.get(Org, org_id)
        if org is not None:
            org.sync_status = "degraded"
            org.sync_error = message
            await session.commit()


async def _record_repo_error(
    session_factory: SessionFactory,
    org_id: int,
    repo_id: int,
    message: str,
    *,
    clone_failure: bool,
) -> None:
    """Persist a repo failure in a fresh session (the work session is dead)."""
    async with session_factory() as session:
        session.add(
            SyncRun(
                org_id=org_id,
                repo_id=repo_id,
                kind="repo",
                status="error",
                error=message[:2000],
                finished_at=_utcnow(),
            )
        )
        repo = await session.get(Repo, repo_id)
        if repo is not None and clone_failure:
            repo.clone_status = CloneStatus.ERROR
            repo.clone_error = message[:2000]
        await session.commit()


async def _sync_repo(
    session_factory: SessionFactory,
    client: GitHubClient,
    org_id: int,
    org_login: str,
    repo_id: int,
    *,
    clone_dir: str | Path,
    token: str | None,
    repo_url_template: str,
    summary: SyncSummary,
) -> None:
    try:
        async with session_factory() as session:
            repo = await session.get_one(Repo, repo_id)
            mirror = GitMirror(clone_dir, org_login, repo.name, token=token)
            url = repo_url_template.format(org=org_login, name=repo.name)
            repo.clone_status = (
                CloneStatus.CLONING if not mirror.exists() else CloneStatus.READY
            )
            await session.flush()

            default_branch = repo.default_branch
            last_oid = repo.last_ingested_oid

            def _extract() -> list[RawCommit]:
                mirror.clone_or_fetch(url, token)
                tip = mirror.branch_tip(default_branch)
                if tip and tip != last_oid:
                    mirror.backfill_blobs()
                    return mirror.log_numstat(default_branch, last_oid)
                return []

            raw: list[RawCommit] = []
            for attempt in range(3):
                try:
                    raw = await asyncio.to_thread(_extract)
                    break
                except GitError as exc:
                    if attempt == 2 or not _is_transient_git_error(exc):
                        raise
                    await asyncio.sleep(10.0 * (attempt + 1))
            repo.clone_status = CloneStatus.READY
            repo.clone_error = None
            repo.last_fetched_at = _utcnow()

            new_commits = 0
            if raw:
                stats = await ingest_repo(session, repo, raw)
                new_commits = stats.new_commits
                if new_commits > 500:
                    # Initial history ingest pulled a lot of blob data; drop it
                    # right away instead of waiting for weekly maintenance.
                    await asyncio.to_thread(mirror.repack)

            prs = await client.pull_requests_since(
                org_login, repo.name, ensure_utc(repo.pr_synced_at)
            )
            pr_stats = await upsert_pull_requests(session, repo, prs)

            session.add(
                SyncRun(
                    org_id=org_id,
                    repo_id=repo_id,
                    kind="repo",
                    status="success",
                    finished_at=_utcnow(),
                    stats={
                        "new_commits": new_commits,
                        "new_prs": pr_stats.new_prs,
                        "updated_prs": pr_stats.updated_prs,
                        "new_reviews": pr_stats.new_reviews,
                    },
                )
            )
            await session.commit()
            summary.repos_processed += 1
            summary.new_commits += new_commits
            summary.new_prs += pr_stats.new_prs
            summary.affected_person_ids.update(pr_stats.affected_person_ids)
    except RateLimitError:
        await _record_repo_error(
            session_factory,
            org_id,
            repo_id,
            "rate limit exhausted",
            clone_failure=False,
        )
        raise
    except AuthError as exc:
        await _record_repo_error(
            session_factory, org_id, repo_id, str(exc), clone_failure=False
        )
        raise
    except Exception as exc:
        await _record_repo_error(
            session_factory, org_id, repo_id, str(exc), clone_failure=True
        )
        raise


async def remove_org(
    session_factory: SessionFactory, org_id: int, *, clone_dir: str | Path
) -> bool:
    """Delete an org and everything derived from it, including clones."""
    async with session_factory() as session:
        org = await session.get(Org, org_id, options=[selectinload(Org.credential)])
        if org is None:
            return False
        org_login = org.login
        await session.delete(org)
        await session.flush()
        await prune_orphans(session)
        await session.commit()
    rmtree_robust(Path(clone_dir) / org_login.lower())
    return True


async def prune_orphans(session: AsyncSession) -> int:
    """Delete identities with no activity left, then persons with no identities."""
    referenced_commit = sa.select(Commit.id).where(
        Commit.author_identity_id == Identity.id
    )
    referenced_pr = sa.select(PullRequest.id).where(
        PullRequest.author_identity_id == Identity.id
    )
    referenced_review = sa.select(Review.id).where(
        Review.reviewer_identity_id == Identity.id
    )
    result: sa.CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        sa.delete(Identity).where(
            ~referenced_commit.exists(),
            ~referenced_pr.exists(),
            ~referenced_review.exists(),
        )
    )
    has_identity = sa.select(Identity.id).where(Identity.person_id == Person.id)
    has_rollup = sa.select(PersonRepoDayStats.person_id).where(
        PersonRepoDayStats.person_id == Person.id
    )
    await session.execute(
        sa.delete(Person).where(~has_identity.exists(), ~has_rollup.exists())
    )
    await session.flush()
    return int(result.rowcount or 0)
