"""Full org sync with a real git fixture and a mocked GitHub API."""

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gca.crypto import encrypt_str
from gca.db.base import Base
from gca.models import (
    Commit,
    Org,
    OrgCredential,
    Person,
    PersonRepoDayStats,
    PullRequest,
    Repo,
    Review,
)
from gca.sync.api import GitHubClient
from gca.sync.orchestrator import remove_org, sync_org

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> None:
    env = {
        "PATH": __import__("os").environ["PATH"],
        "GIT_AUTHOR_NAME": "Alice Dev",
        "GIT_AUTHOR_EMAIL": "alice@example.com",
        "GIT_COMMITTER_NAME": "Alice Dev",
        "GIT_COMMITTER_EMAIL": "alice@example.com",
        "GIT_AUTHOR_DATE": "2024-02-01T10:00:00+00:00",
        "GIT_COMMITTER_DATE": "2024-02-01T10:00:00+00:00",
    }
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    src = tmp_path / "widget-src"
    src.mkdir()
    _git(src, "init", "-b", "main")
    _git(src, "config", "uploadpack.allowfilter", "true")
    (src / "main.py").write_text("print('hello')\n" * 5)
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "feat: hello")
    return src


@pytest.fixture
async def factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _graphql(data: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json={"data": data})


def _make_handler() -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = request.content.decode()
        if "organization(login" in body and "repositories" not in body:
            return _graphql(
                {
                    "organization": {
                        "login": "acme",
                        "name": "Acme",
                        "id": "O_1",
                        "avatarUrl": "",
                    },
                    "repositoryOwner": {"__typename": "Organization"},
                }
            )
        if "repositories(first" in body:
            return _graphql(
                {
                    "organization": {
                        "repositories": {
                            "nodes": [
                                {
                                    "name": "widget",
                                    "id": "R_1",
                                    "isPrivate": True,
                                    "isArchived": False,
                                    "isFork": False,
                                    "defaultBranchRef": {"name": "main"},
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            )
        if "pullRequests(" in body:
            return _graphql(
                {
                    "repository": {
                        "pullRequests": {
                            "nodes": [
                                {
                                    "number": 1,
                                    "id": "PR_1",
                                    "title": "add hello",
                                    "state": "MERGED",
                                    "createdAt": "2024-02-01T09:00:00Z",
                                    "updatedAt": "2024-02-02T10:00:00Z",
                                    "mergedAt": "2024-02-02T10:00:00Z",
                                    "closedAt": "2024-02-02T10:00:00Z",
                                    "additions": 5,
                                    "deletions": 0,
                                    "changedFiles": 1,
                                    "author": {"login": "alicedev", "id": "U_1"},
                                    "reviews": {
                                        "nodes": [
                                            {
                                                "id": "RV_1",
                                                "state": "APPROVED",
                                                "submittedAt": "2024-02-01T12:00:00Z",
                                                "author": {
                                                    "login": "bobbuilder",
                                                    "id": "U_2",
                                                },
                                            }
                                        ]
                                    },
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            )
        raise AssertionError(f"unexpected request: {body[:120]}")

    return handler


async def _seed_org(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        org = Org(login="acme", display_name="acme")
        session.add(org)
        await session.flush()
        session.add(
            OrgCredential(org_id=org.id, token_encrypted=encrypt_str("tok-123"))
        )
        await session.commit()
        return org.id


async def test_sync_org_end_to_end(
    tmp_path: Path,
    source_repo: Path,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    org_id = await _seed_org(factory)
    clone_dir = tmp_path / "clones"

    def client_factory(token: str) -> GitHubClient:
        assert token == "tok-123"
        return GitHubClient(token, transport=httpx2.MockTransport(_make_handler()))

    summary = await sync_org(
        factory,
        org_id,
        clone_dir=clone_dir,
        client_factory=client_factory,
        repo_url_template=source_repo.as_uri().replace("widget-src", "widget-src"),
    )
    assert summary.errors == []
    assert summary.repos_processed == 1
    assert summary.new_commits == 1
    assert summary.new_prs == 1

    async with factory() as session:
        org = await session.get_one(Org, org_id)
        assert org.sync_status == "idle"
        repo = (
            await session.execute(sa.select(Repo).where(Repo.org_id == org_id))
        ).scalar_one()
        assert repo.clone_status.value == "ready"
        assert repo.last_ingested_oid is not None
        assert repo.pr_synced_at is not None
        assert (
            await session.execute(sa.select(sa.func.count()).select_from(Commit))
        ).scalar_one() == 1
        assert (
            await session.execute(sa.select(sa.func.count()).select_from(PullRequest))
        ).scalar_one() == 1
        assert (
            await session.execute(sa.select(sa.func.count()).select_from(Review))
        ).scalar_one() == 1
        rollups = (await session.execute(sa.select(PersonRepoDayStats))).scalars().all()
        assert sum(r.prs_opened for r in rollups) == 1
        assert sum(r.reviews for r in rollups) == 1
        assert sum(r.commits for r in rollups) == 1

    # Second sync is incremental and quiet.
    second = await sync_org(
        factory,
        org_id,
        clone_dir=clone_dir,
        client_factory=client_factory,
        repo_url_template=source_repo.as_uri(),
    )
    assert second.errors == []
    assert second.new_commits == 0


async def test_remove_org_cascades(
    tmp_path: Path,
    source_repo: Path,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    org_id = await _seed_org(factory)
    clone_dir = tmp_path / "clones"

    def client_factory(token: str) -> GitHubClient:
        return GitHubClient(token, transport=httpx2.MockTransport(_make_handler()))

    await sync_org(
        factory,
        org_id,
        clone_dir=clone_dir,
        client_factory=client_factory,
        repo_url_template=source_repo.as_uri(),
    )
    assert (clone_dir / "acme" / "widget.git").exists()

    assert await remove_org(factory, org_id, clone_dir=clone_dir) is True
    assert not (clone_dir / "acme").exists()
    async with factory() as session:
        for model in (Org, Repo, Commit, PullRequest, Review, PersonRepoDayStats):
            count = (
                await session.execute(sa.select(sa.func.count()).select_from(model))
            ).scalar_one()
            assert count == 0, model.__name__
        persons = (
            await session.execute(sa.select(sa.func.count()).select_from(Person))
        ).scalar_one()
        assert persons == 0
