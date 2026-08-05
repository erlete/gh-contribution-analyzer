"""Worker job implementations."""

import asyncio
import logging
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gca.config import get_settings
from gca.identity import suggest
from gca.mail.dispatch import send_report
from gca.models import (
    CloneStatus,
    JobLedger,
    Org,
    Recipient,
    Repo,
    ReportSchedule,
)
from gca.reports.service import generate_reports
from gca.scheduler.periods import period_key, period_label, previous_period
from gca.services.settings import SettingsStore
from gca.sync.gitrepo import GitMirror, rmtree_robust
from gca.sync.orchestrator import sync_org

log = logging.getLogger("gca.worker")

SessionFactory = async_sessionmaker[AsyncSession]
SYNC_REQUESTS_KEY = "sync_requests"


async def reset_stale_runs(factory: SessionFactory) -> None:
    """After a crash, orgs stuck in `running` block every future sync."""
    async with factory() as session:
        await session.execute(
            sa.update(Org)
            .where(Org.sync_status == "running")
            .values(sync_status="idle")
        )
        await session.commit()


async def sync_all_orgs(factory: SessionFactory) -> None:
    async with factory() as session:
        org_ids = [
            row.id
            for row in (
                await session.execute(
                    sa.select(Org.id).where(
                        Org.sync_enabled.is_(True), Org.sync_status != "running"
                    )
                )
            ).all()
        ]
    for org_id in org_ids:
        summary = await sync_org(factory, org_id, clone_dir=get_settings().clone_dir)
        log.info(
            "sync org=%s repos=%s commits=%s prs=%s errors=%s",
            org_id,
            summary.repos_processed,
            summary.new_commits,
            summary.new_prs,
            summary.errors,
        )
    if org_ids:
        async with factory() as session:
            await suggest.generate(session)
            await session.commit()


async def check_sync_requests(factory: SessionFactory) -> None:
    async with factory() as session:
        store = SettingsStore(session)
        requests = await store.get(SYNC_REQUESTS_KEY) or {}
        if not requests:
            return
        await store.set(SYNC_REQUESTS_KEY, {})
        await session.commit()
        pending = [int(key) for key in requests if key.isdigit()]
    for org_id in pending:
        async with factory() as session:
            org = await session.get(Org, org_id)
            if org is None or org.sync_status == "running":
                continue
        log.info("manual sync requested for org=%s", org_id)
        await sync_org(factory, org_id, clone_dir=get_settings().clone_dir)


async def _claim(factory: SessionFactory, job: str, period: str) -> bool:
    async with factory() as session:
        session.add(JobLedger(job_key=job, period_key=period))
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


async def period_watcher(factory: SessionFactory, today: date | None = None) -> None:
    """Generate and email reports for periods that just closed. Never
    retroactive: only the most recent closed period per schedule is
    considered, and the ledger makes each one fire exactly once."""
    today = today or date.today()
    async with factory() as session:
        schedules = (
            (
                await session.execute(
                    sa.select(ReportSchedule).where(ReportSchedule.enabled.is_(True))
                )
            )
            .scalars()
            .all()
        )
        schedule_data = [
            (s.period_kind, s.report_kind, list(s.org_scope or [])) for s in schedules
        ]
    for kind, report_kind, org_scope in schedule_data:
        start, end = previous_period(kind, today)
        job = f"report:{kind}:{report_kind}"
        if not await _claim(factory, job, period_key(kind, start)):
            continue
        log.info("generating %s reports for %s", report_kind, period_key(kind, start))
        async with factory() as session:
            reports = await generate_reports(
                session,
                kind=report_kind,
                org_ids=org_scope,
                start=start,
                end=end,
                period_kind=kind,
                period_label=period_label(kind, start),
            )
            recipients = [
                r.email
                for r in (
                    await session.execute(
                        sa.select(Recipient).where(Recipient.active.is_(True))
                    )
                ).scalars()
            ]
            for report in reports:
                if recipients:
                    await send_report(session, report, recipients)
            await session.commit()


async def generate_suggestions_job(factory: SessionFactory) -> None:
    async with factory() as session:
        created = await suggest.generate(session)
        await session.commit()
        if created:
            log.info("generated %s merge suggestions", created)


async def maintenance(factory: SessionFactory) -> None:
    """Weekly: filtered repack keeps clones slim, orphan dirs are removed."""
    settings = get_settings()
    clone_root = Path(settings.clone_dir)
    async with factory() as session:
        rows = (
            await session.execute(
                sa.select(Repo.name, Org.login)
                .join(Org, Repo.org_id == Org.id)
                .where(Repo.clone_status == CloneStatus.READY)
            )
        ).all()
        valid_logins = {
            row.login.lower()
            for row in (await session.execute(sa.select(Org.login))).all()
        }
    for name, login in rows:
        mirror = GitMirror(clone_root, login, name)
        if mirror.exists():
            try:
                await asyncio.to_thread(mirror.repack)
            except Exception as exc:
                log.warning("repack failed for %s/%s: %s", login, name, exc)
    if clone_root.exists():
        for child in clone_root.iterdir():
            if child.is_dir() and child.name.lower() not in valid_logins:
                log.info("removing orphan clone dir %s", child)
                rmtree_robust(child)
