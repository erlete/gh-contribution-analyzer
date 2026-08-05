from collections.abc import AsyncIterator
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gca.db.base import Base
from gca.models import Report
from gca.reports.service import request_report
from gca.scheduler import jobs


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_request_report_creates_queued_row(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        report = await request_report(
            session,
            kind="person",
            org_ids=[1, 2],
            start=date(2026, 7, 1),
            end=date(2026, 8, 1),
            repo_ids=[5],
            person_ids=[7, 8],
        )
        await session.commit()
        assert report.status == "queued"
        assert report.pdf_path == ""
        assert report.title.startswith("Individual contributor report")
        assert report.params == {
            "period_label": "2026-07-01 to 2026-08-01",
            "repo_ids": [5],
            "person_ids": [7, 8],
        }


async def test_request_report_rejects_unknown_kind(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        with pytest.raises(ValueError, match="unknown report kind"):
            await request_report(
                session,
                kind="bogus",
                org_ids=[],
                start=date(2026, 7, 1),
                end=date(2026, 8, 1),
            )


async def test_queue_processes_and_marks_generated(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with factory() as session:
        await request_report(
            session,
            kind="overview",
            org_ids=[],
            start=date(2026, 7, 1),
            end=date(2026, 8, 1),
        )
        await session.commit()

    async def fake_fulfill(session: AsyncSession, report: Report) -> None:
        report.pdf_path = "2026/07/fake.pdf"
        report.status = "generated"

    monkeypatch.setattr(jobs, "fulfill_report", fake_fulfill)
    await jobs.process_report_queue(factory)
    async with factory() as session:
        row = (await session.execute(sa.select(Report))).scalar_one()
        assert row.status == "generated"
        assert row.pdf_path == "2026/07/fake.pdf"


async def test_queue_marks_failed_on_error(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with factory() as session:
        await request_report(
            session,
            kind="repo",
            org_ids=[],
            start=date(2026, 7, 1),
            end=date(2026, 8, 1),
        )
        await session.commit()

    async def broken_fulfill(session: AsyncSession, report: Report) -> None:
        raise RuntimeError("chart renderer exploded")

    monkeypatch.setattr(jobs, "fulfill_report", broken_fulfill)
    await jobs.process_report_queue(factory)
    async with factory() as session:
        row = (await session.execute(sa.select(Report))).scalar_one()
        assert row.status == "failed"
        assert row.error is not None and "chart renderer exploded" in row.error


async def test_stale_generating_requeued_at_startup(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        report = await request_report(
            session,
            kind="overview",
            org_ids=[],
            start=date(2026, 7, 1),
            end=date(2026, 8, 1),
        )
        report.status = "generating"
        await session.commit()
    await jobs.reset_stale_runs(factory)
    async with factory() as session:
        row = (await session.execute(sa.select(Report))).scalar_one()
        assert row.status == "queued"
