"""End-to-end git extraction and ingestion against a scripted repository."""

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.models import Commit, Org, PersonRepoDayStats, Repo
from gca.sync.gitrepo import GitMirror
from gca.sync.ingest import ingest_repo

pytestmark = pytest.mark.integration

ALICE = ("Alice Dev", "alice@example.com")
BOB = ("Bob Builder", "bob@example.com")


def _git(
    cwd: Path, *args: str, author: tuple[str, str] | None = None, date: str = ""
) -> str:
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": __import__("os").environ["PATH"],
        "HOME": str(cwd),
    }
    if author:
        env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"] = author
        env["GIT_COMMITTER_NAME"], env["GIT_COMMITTER_EMAIL"] = author
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, f"git {args}: {result.stderr}"
    return result.stdout.strip()


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    src = tmp_path / "origin"
    src.mkdir()
    _git(src, "init", "-b", "main")
    _git(src, "config", "user.name", "Test")
    _git(src, "config", "user.email", "test@example.com")
    _git(src, "config", "uploadpack.allowfilter", "true")

    (src / "app.py").write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
    (src / "README.md").write_text("# Readme\n" + "text\n" * 4)
    _git(src, "add", "-A")
    _git(
        src,
        "commit",
        "-m",
        "feat: initial code",
        author=ALICE,
        date="2024-01-01T10:00:00+00:00",
    )

    # Bob rewrites 4 of Alice's lines and adds 2 within the churn window.
    (src / "app.py").write_text(
        "\n".join(f"line {i}" for i in range(6))
        + "\nchanged a\nchanged b\nchanged c\nchanged d\nextra 1\nextra 2\n"
    )
    _git(src, "add", "-A")
    _git(
        src,
        "commit",
        "-m",
        "fix: rework tail",
        author=BOB,
        date="2024-01-10T10:00:00+00:00",
    )

    # Alice deletes two lines far outside the window: no churn.
    content = (src / "app.py").read_text().splitlines()
    (src / "app.py").write_text("\n".join(content[:-2]) + "\n")
    _git(src, "add", "-A")
    _git(
        src,
        "commit",
        "-m",
        "chore: prune",
        author=ALICE,
        date="2024-03-15T10:00:00+00:00",
    )

    # Pure rename by Bob.
    _git(src, "mv", "app.py", "core.py")
    _git(
        src,
        "commit",
        "-m",
        "refactor: rename module",
        author=BOB,
        date="2024-03-20T10:00:00+00:00",
    )

    # Merge commit.
    _git(src, "checkout", "-b", "feature", date="2024-04-01T10:00:00+00:00")
    (src / "feature.py").write_text("print('x')\n")
    _git(src, "add", "-A")
    _git(
        src,
        "commit",
        "-m",
        "feat: feature branch",
        author=ALICE,
        date="2024-04-01T10:00:00+00:00",
    )
    _git(src, "checkout", "main")
    _git(
        src,
        "merge",
        "--no-ff",
        "feature",
        "-m",
        "merge feature",
        date="2024-04-02T10:00:00+00:00",
    )
    return src


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed_repo(session: AsyncSession) -> Repo:
    org = Org(login="acme", display_name="Acme")
    session.add(org)
    await session.flush()
    repo = Repo(org_id=org.id, name="widget", default_branch="main")
    session.add(repo)
    await session.flush()
    return repo


async def test_full_extraction_and_ingest(
    tmp_path: Path, source_repo: Path, session: AsyncSession
) -> None:
    mirror = GitMirror(tmp_path / "clones", "acme", "widget")
    mirror.clone_or_fetch(source_repo.as_uri())
    commits = mirror.log_numstat("main")
    assert len(commits) == 6

    first, rework, prune, rename, feature, merge = commits
    assert first.author_email == "alice@example.com"
    assert sum(f.additions for f in first.files) == 15
    assert merge.is_merge and merge.files == []
    rename_file = rename.files[0]
    assert rename_file.old_path == "app.py"
    assert rename_file.path == "core.py"
    assert rename_file.additions == 0 and rename_file.deletions == 0

    repo = await _seed_repo(session)
    stats = await ingest_repo(session, repo, commits)
    assert stats.new_commits == 6
    assert repo.last_ingested_oid == merge.oid

    rows = {
        row.oid: row
        for row in (
            await session.execute(sa.select(Commit).where(Commit.repo_id == repo.id))
        ).scalars()
    }
    rework_row = rows[rework.oid]
    # Bob deleted 4 lines that Alice added 9 days earlier: all cross churn.
    assert rework_row.churn_lines == 4
    assert rework_row.cross_churn_lines == 4
    assert rework_row.self_churn_lines == 0

    prune_row = rows[prune.oid]
    assert prune_row.churn_lines == 0

    merge_row = rows[merge.oid]
    assert merge_row.is_merge and merge_row.significance == 0.0

    day_stats = (await session.execute(sa.select(PersonRepoDayStats))).scalars().all()
    assert sum(d.commits for d in day_stats) == 6
    alice_first_day = [d for d in day_stats if d.day.isoformat() == "2024-01-01"]
    assert len(alice_first_day) == 1
    assert alice_first_day[0].additions == 15


async def test_incremental_ingest(
    tmp_path: Path, source_repo: Path, session: AsyncSession
) -> None:
    mirror = GitMirror(tmp_path / "clones", "acme", "widget")
    mirror.clone_or_fetch(source_repo.as_uri())
    commits = mirror.log_numstat("main")
    repo = await _seed_repo(session)
    await ingest_repo(session, repo, commits)
    tip = repo.last_ingested_oid
    assert tip is not None

    (source_repo / "new.py").write_text("a = 1\n")
    _git(source_repo, "add", "-A")
    _git(
        source_repo,
        "commit",
        "-m",
        "feat: new module",
        author=ALICE,
        date="2024-05-01T10:00:00+00:00",
    )
    mirror.clone_or_fetch(source_repo.as_uri())
    fresh = mirror.log_numstat("main", since_oid=tip)
    assert len(fresh) == 1
    stats = await ingest_repo(session, repo, fresh)
    assert stats.new_commits == 1
    assert repo.last_ingested_oid == fresh[0].oid

    # Re-running the same batch is a no-op.
    again = await ingest_repo(session, repo, fresh)
    assert again.new_commits == 0
