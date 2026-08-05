"""Admin CLI: headless org management, syncs and report generation.

Tokens are read from files or stdin, never from argv, so they cannot leak
into shell history or process listings.
"""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from gca.config import get_settings
from gca.db.engine import get_session_factory
from gca.identity import suggest
from gca.models import Org, Report
from gca.reports.service import generate_reports
from gca.services.orgs import add_org
from gca.services.settings import SettingsStore
from gca.sync.orchestrator import remove_org, sync_org


async def _cmd_org_add(login: str, token_file: str | None) -> int:
    if token_file:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    else:
        token = sys.stdin.readline().strip()
    if not token:
        print("no token provided")
        return 1
    factory = get_session_factory()
    async with factory() as session:
        try:
            org = await add_org(session, login, token)
            await session.commit()
        except Exception as exc:
            print(f"failed: {exc}")
            return 1
        print(f"connected organization {org.login} (id {org.id})")
    return 0


async def _cmd_org_list() -> int:
    factory = get_session_factory()
    async with factory() as session:
        orgs = (
            (
                await session.execute(
                    sa.select(Org)
                    .options(selectinload(Org.credential))
                    .order_by(Org.login)
                )
            )
            .scalars()
            .all()
        )
        for org in orgs:
            validated = (
                org.credential.validated_at.isoformat()
                if org.credential and org.credential.validated_at
                else "never"
            )
            print(
                f"{org.id}\t{org.login}\t{org.sync_status}"
                f"\tvalidated={validated}\tlast_sync={org.last_synced_at or 'never'}"
            )
        if not orgs:
            print("no organizations configured")
    return 0


async def _cmd_org_remove(login: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        org = (
            await session.execute(sa.select(Org).where(Org.login.ilike(login)))
        ).scalar_one_or_none()
        if org is None:
            print(f"organization {login} not found")
            return 1
        org_id = org.id
    removed = await remove_org(factory, org_id, clone_dir=get_settings().clone_dir)
    print("removed" if removed else "nothing removed")
    return 0


async def _cmd_sync(login: str | None) -> int:
    factory = get_session_factory()
    async with factory() as session:
        query = sa.select(Org.id, Org.login)
        if login:
            query = query.where(Org.login.ilike(login))
        targets = (await session.execute(query.order_by(Org.login))).all()
    if not targets:
        print("no matching organizations")
        return 1
    exit_code = 0
    for org_id, org_login in targets:
        print(f"syncing {org_login} ...")
        summary = await sync_org(factory, org_id, clone_dir=get_settings().clone_dir)
        print(
            f"  repos={summary.repos_processed} commits={summary.new_commits}"
            f" prs={summary.new_prs} degraded={summary.degraded}"
        )
        for error in summary.errors:
            print(f"  error: {error}")
            exit_code = 1
    async with factory() as session:
        created, removed = await suggest.generate(session)
        await session.commit()
        print(f"merge suggestions: {created} created, {removed} stale removed")
    return exit_code


async def _cmd_report(kind: str, date_from: str | None, date_to: str | None) -> int:
    factory = get_session_factory()
    start = date.fromisoformat(date_from) if date_from else date(2008, 1, 1)
    end = date.fromisoformat(date_to) if date_to else date.today()
    async with factory() as session:
        reports = await generate_reports(
            session, kind=kind, org_ids=[], start=start, end=end
        )
        await session.commit()
        for report in reports:
            print(f"{report.id}\t{report.title}\t{report.pdf_path}")
        print(f"{len(reports)} report(s) generated")
    return 0


async def _cmd_report_list() -> int:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    sa.select(Report).order_by(Report.generated_at.desc()).limit(50)
                )
            )
            .scalars()
            .all()
        )
        for report in rows:
            print(
                f"{report.id}\t{report.kind}\t{report.status}"
                f"\t{report.title}\t{report.pdf_path}"
            )
    return 0


async def _cmd_suggest() -> int:
    factory = get_session_factory()
    async with factory() as session:
        created, removed = await suggest.generate(session)
        await session.commit()
        print(f"{created} suggestions created, {removed} stale removed")
    return 0


async def _cmd_seed() -> int:
    factory = get_session_factory()
    async with factory() as session:
        await SettingsStore(session).seed_from_env(get_settings())
        await session.commit()
        print("settings seeded from environment where unset")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="gca")
    sub = parser.add_subparsers(dest="command", required=True)

    org = sub.add_parser("org", help="manage organizations")
    org_sub = org.add_subparsers(dest="org_command", required=True)
    org_add = org_sub.add_parser("add")
    org_add.add_argument("login")
    org_add.add_argument(
        "--token-file", help="file containing the PAT; stdin when omitted"
    )
    org_sub.add_parser("list")
    org_remove = org_sub.add_parser("remove")
    org_remove.add_argument("login")

    sync = sub.add_parser("sync", help="run syncs")
    sync_sub = sync.add_subparsers(dest="sync_command", required=True)
    sync_run = sync_sub.add_parser("run")
    sync_run.add_argument("--org", default=None)

    report = sub.add_parser("report", help="generate reports")
    report_sub = report.add_subparsers(dest="report_command", required=True)
    report_generate = report_sub.add_parser("generate")
    report_generate.add_argument(
        "--kind", choices=("overview", "person", "repo"), default="overview"
    )
    report_generate.add_argument("--from", dest="date_from", default=None)
    report_generate.add_argument("--to", dest="date_to", default=None)
    report_sub.add_parser("list")

    suggest_parser = sub.add_parser("suggest", help="identity suggestions")
    suggest_sub = suggest_parser.add_subparsers(dest="suggest_command", required=True)
    suggest_sub.add_parser("run")

    settings_parser = sub.add_parser("settings", help="settings management")
    settings_sub = settings_parser.add_subparsers(
        dest="settings_command", required=True
    )
    settings_sub.add_parser("seed")

    args = parser.parse_args()
    if args.command == "org" and args.org_command == "add":
        code = asyncio.run(_cmd_org_add(args.login, args.token_file))
    elif args.command == "org" and args.org_command == "list":
        code = asyncio.run(_cmd_org_list())
    elif args.command == "org" and args.org_command == "remove":
        code = asyncio.run(_cmd_org_remove(args.login))
    elif args.command == "sync":
        code = asyncio.run(_cmd_sync(args.org))
    elif args.command == "report" and args.report_command == "generate":
        code = asyncio.run(_cmd_report(args.kind, args.date_from, args.date_to))
    elif args.command == "report" and args.report_command == "list":
        code = asyncio.run(_cmd_report_list())
    elif args.command == "suggest":
        code = asyncio.run(_cmd_suggest())
    else:
        code = asyncio.run(_cmd_seed())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
