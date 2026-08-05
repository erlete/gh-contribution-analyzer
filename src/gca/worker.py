"""Worker entrypoint: APScheduler-driven syncs, reports and maintenance."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from gca.db.engine import get_session_factory
from gca.scheduler import jobs

log = logging.getLogger("gca.worker")


async def _run() -> None:
    factory = get_session_factory()
    await jobs.reset_stale_runs(factory)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        jobs.sync_all_orgs,
        IntervalTrigger(hours=6, jitter=300),
        args=[factory],
        next_run_time=datetime.now(UTC) + timedelta(seconds=20),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.check_sync_requests,
        IntervalTrigger(seconds=30),
        args=[factory],
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.process_report_queue,
        IntervalTrigger(seconds=15),
        args=[factory],
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.period_watcher,
        CronTrigger(hour=0, minute=15),
        args=[factory],
        max_instances=1,
    )
    scheduler.add_job(
        jobs.generate_suggestions_job,
        CronTrigger(hour=3, minute=0),
        args=[factory],
        max_instances=1,
    )
    scheduler.add_job(
        jobs.maintenance,
        CronTrigger(day_of_week="sun", hour=4, minute=0),
        args=[factory],
        max_instances=1,
    )
    scheduler.start()
    log.info("worker scheduler started")
    await asyncio.Event().wait()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
