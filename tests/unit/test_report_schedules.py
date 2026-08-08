"""Default report schedules: on unless the user opted out."""

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gca.db.base import Base
from gca.models import ReportSchedule
from gca.reports.service import REPORT_KINDS, ensure_default_schedules
from gca.scheduler.periods import PERIOD_KINDS


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_fresh_database_gets_every_combination_enabled(
    session: AsyncSession,
) -> None:
    created = await ensure_default_schedules(session)
    await session.commit()
    rows = (await session.execute(sa.select(ReportSchedule))).scalars().all()
    assert created == len(PERIOD_KINDS) * len(REPORT_KINDS)
    assert len(rows) == created
    assert all(r.enabled for r in rows)


async def test_saved_choices_survive_reseeding(session: AsyncSession) -> None:
    session.add(
        ReportSchedule(
            period_kind=PERIOD_KINDS[0], report_kind=REPORT_KINDS[0], enabled=False
        )
    )
    await session.flush()
    created = await ensure_default_schedules(session)
    await session.commit()
    assert created == len(PERIOD_KINDS) * len(REPORT_KINDS) - 1
    opted_out = (
        await session.execute(
            sa.select(ReportSchedule).where(
                ReportSchedule.period_kind == PERIOD_KINDS[0],
                ReportSchedule.report_kind == REPORT_KINDS[0],
            )
        )
    ).scalar_one()
    assert opted_out.enabled is False


async def test_reseeding_is_idempotent(session: AsyncSession) -> None:
    await ensure_default_schedules(session)
    await session.flush()
    assert await ensure_default_schedules(session) == 0
