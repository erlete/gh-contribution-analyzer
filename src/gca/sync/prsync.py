"""Pull request and review upserts."""

from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.identity.resolver import get_or_create_github_identity
from gca.models import PullRequest, Repo, Review
from gca.sync.api import PRInfo
from gca.timeutil import ensure_utc


@dataclass
class PrSyncStats:
    new_prs: int = 0
    updated_prs: int = 0
    new_reviews: int = 0
    affected_person_ids: set[int] = field(default_factory=set)


async def upsert_pull_requests(
    session: AsyncSession, repo: Repo, prs: list[PRInfo]
) -> PrSyncStats:
    stats = PrSyncStats()
    if not prs:
        return stats
    for info in prs:
        author_identity_id: int | None = None
        if info.author_login:
            identity = await get_or_create_github_identity(
                session,
                login=info.author_login,
                node_id=info.author_node_id,
                commit_on_create=True,
            )
            author_identity_id = identity.id
            stats.affected_person_ids.add(identity.person_id)

        existing = (
            await session.execute(
                sa.select(PullRequest).where(
                    PullRequest.repo_id == repo.id,
                    PullRequest.number == info.number,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = PullRequest(
                repo_id=repo.id,
                number=info.number,
                node_id=info.node_id or None,
                created_at_gh=info.created_at,
            )
            session.add(existing)
            stats.new_prs += 1
        else:
            stats.updated_prs += 1
        existing.author_identity_id = author_identity_id
        existing.title = info.title[:500]
        existing.state = info.state[:10]
        existing.created_at_gh = info.created_at
        existing.merged_at = info.merged_at
        existing.closed_at = info.closed_at
        existing.additions = info.additions
        existing.deletions = info.deletions
        existing.changed_files = info.changed_files
        await session.flush()

        known_review_ids = {
            row.node_id
            for row in (
                await session.execute(
                    sa.select(Review.node_id).where(
                        Review.pull_request_id == existing.id
                    )
                )
            ).all()
            if row.node_id
        }
        for review in info.reviews:
            if not review.node_id or review.node_id in known_review_ids:
                continue
            reviewer_identity_id: int | None = None
            if review.login:
                reviewer = await get_or_create_github_identity(
                    session,
                    login=review.login,
                    node_id=review.author_node_id,
                    commit_on_create=True,
                )
                reviewer_identity_id = reviewer.id
                stats.affected_person_ids.add(reviewer.person_id)
            session.add(
                Review(
                    pull_request_id=existing.id,
                    node_id=review.node_id or None,
                    reviewer_identity_id=reviewer_identity_id,
                    state=review.state[:30],
                    submitted_at=review.submitted_at,
                )
            )
            stats.new_reviews += 1
        await session.flush()

    newest = max(p.updated_at for p in prs)
    current = ensure_utc(repo.pr_synced_at)
    if current is None or newest > current:
        repo.pr_synced_at = newest
    await session.flush()
    return stats
