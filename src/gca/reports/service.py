"""Report generation service."""

from datetime import UTC, date, datetime, time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.ai.insights import insight_for
from gca.config import get_settings
from gca.metrics.churn import DEFAULT_CHURN_WINDOW_DAYS
from gca.models import Org, Person, Repo, Report
from gca.reports.builder import render_pdf, trend_chart_data_uri
from gca.services import stats
from gca.timeutil import utcnow

REPORT_KINDS = ("overview", "person", "repo")


async def _scope_label(session: AsyncSession, org_ids: list[int]) -> str:
    query = sa.select(Org.login).order_by(Org.login)
    if org_ids:
        query = query.where(Org.id.in_(org_ids))
    logins = [row.login for row in (await session.execute(query)).all()]
    return ", ".join(logins) if logins else "no orgs"


def _report_path(kind: str, slug: str, when: date) -> str:
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{when.year:04d}/{when.month:02d}/{kind}-{slug}-{stamp}.pdf"


async def _narrative(
    session: AsyncSession,
    *,
    view: str,
    scope_key: str,
    period_key: str,
    context: dict[str, object],
) -> str | None:
    return await insight_for(
        session,
        view=view,
        scope_key=scope_key,
        period_key=period_key,
        context=context,
    )


async def generate_reports(
    session: AsyncSession,
    *,
    kind: str,
    org_ids: list[int],
    start: date,
    end: date,
    period_kind: str = "custom",
    period_label: str | None = None,
    repo_ids: list[int] | None = None,
    person_ids: list[int] | None = None,
    reports_dir: str | Path | None = None,
) -> list[Report]:
    """Generate one or many PDF reports and persist their rows."""
    if kind not in REPORT_KINDS:
        raise ValueError(f"unknown report kind: {kind}")
    base_dir = Path(reports_dir or get_settings().reports_dir)
    scope_label = await _scope_label(session, org_ids)
    label = period_label or f"{start.isoformat()} to {end.isoformat()}"
    generated_at = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    common = {
        "period_label": label,
        "scope_label": scope_label,
        "generated_at": generated_at,
        "churn_window": DEFAULT_CHURN_WINDOW_DAYS,
    }
    scope_key = ",".join(str(i) for i in sorted(org_ids)) or "all"
    period_key = f"{period_kind}:{start.isoformat()}:{end.isoformat()}"
    reports: list[Report] = []

    if kind == "overview":
        totals = await stats.totals(
            session, orgs=org_ids, start=start, end=end, repo_ids=repo_ids
        )
        people = await stats.person_leaderboard(
            session,
            orgs=org_ids,
            start=start,
            end=end,
            repo_ids=repo_ids,
            person_ids=person_ids,
        )
        repos = await stats.repo_leaderboard(
            session,
            orgs=org_ids,
            start=start,
            end=end,
            repo_ids=repo_ids,
            person_ids=person_ids,
        )
        series = await stats.timeseries(
            session, orgs=org_ids, start=start, end=end, repo_ids=repo_ids
        )
        narrative = await _narrative(
            session,
            view="report:overview",
            scope_key=scope_key,
            period_key=period_key,
            context={
                "totals": totals.__dict__,
                "top": [
                    {"name": p.display_name, "significance": round(p.significance, 1)}
                    for p in people[:5]
                ],
            },
        )
        title = f"Contribution overview, {label}"
        pdf = render_pdf(
            "overview.html",
            {
                **common,
                "title": title,
                "totals": totals,
                "people": people,
                "repos": repos,
                "chart": trend_chart_data_uri(series) if series else None,
                "narrative": narrative,
            },
        )
        reports.append(
            await _store(
                session,
                pdf,
                base_dir,
                kind="overview",
                slug="all",
                title=title,
                period_kind=period_kind,
                start=start,
                end=end,
                org_ids=org_ids,
            )
        )
        return reports

    if kind == "person":
        board = await stats.person_leaderboard(
            session, orgs=org_ids, start=start, end=end, repo_ids=repo_ids
        )
        subjects = (
            [s for s in board if s.person_id in set(person_ids)]
            if person_ids
            else board
        )
        for me in subjects:
            person = await session.get_one(Person, me.person_id)
            split = await stats.person_repo_split(
                session, person_id=me.person_id, orgs=org_ids, start=start, end=end
            )
            series = await stats.timeseries(
                session,
                orgs=org_ids,
                start=start,
                end=end,
                person_ids=[me.person_id],
            )
            narrative = await _narrative(
                session,
                view=f"report:person:{me.person_id}",
                scope_key=scope_key,
                period_key=period_key,
                context={
                    "person": person.display_name,
                    "commits": me.commits,
                    "significance": round(me.significance, 1),
                    "churn_ratio": round(me.churn_ratio, 3),
                    "percentile_significance": me.percentiles.get("significance"),
                },
            )
            title = f"{person.display_name}, {label}"
            metric_rows = [
                ("commits", "Commits", f"{me.commits:,}"),
                ("additions", "Lines added", f"{me.additions:,}"),
                ("churn", "Churn lines", f"{me.churn:,}"),
                ("significance", "Significance", f"{me.significance:.1f}"),
                ("prs_merged", "PRs merged", f"{me.prs_merged:,}"),
                ("reviews", "Reviews", f"{me.reviews:,}"),
            ]
            pdf = render_pdf(
                "person.html",
                {
                    **common,
                    "title": title,
                    "me": me,
                    "population": len(board),
                    "metric_rows": metric_rows,
                    "split": split,
                    "chart": trend_chart_data_uri(series) if series else None,
                    "narrative": narrative,
                },
            )
            reports.append(
                await _store(
                    session,
                    pdf,
                    base_dir,
                    kind="person",
                    slug=f"p{me.person_id}",
                    title=title,
                    period_kind=period_kind,
                    start=start,
                    end=end,
                    org_ids=org_ids,
                    subject_type="person",
                    subject_id=me.person_id,
                )
            )
        return reports

    board_r = await stats.repo_leaderboard(
        session, orgs=org_ids, start=start, end=end, person_ids=person_ids
    )
    subjects_r = (
        [s for s in board_r if s.repo_id in set(repo_ids)] if repo_ids else board_r
    )
    for repo_stat in subjects_r:
        repo = await session.get_one(Repo, repo_stat.repo_id)
        totals = await stats.totals(
            session, orgs=[], start=start, end=end, repo_ids=[repo.id]
        )
        contributors = await stats.repo_contributors(
            session, repo_id=repo.id, orgs=[], start=start, end=end
        )
        series = await stats.timeseries(
            session, orgs=[], start=start, end=end, repo_ids=[repo.id]
        )
        narrative = await _narrative(
            session,
            view=f"report:repo:{repo.id}",
            scope_key=scope_key,
            period_key=period_key,
            context={
                "repo": f"{repo_stat.org_login}/{repo.name}",
                "commits": totals.commits,
                "significance": round(totals.significance, 1),
                "contributors": len(contributors),
            },
        )
        title = f"{repo_stat.org_login}/{repo.name}, {label}"
        pdf = render_pdf(
            "repo.html",
            {
                **common,
                "title": title,
                "totals": totals,
                "contributors": contributors,
                "chart": trend_chart_data_uri(series) if series else None,
                "narrative": narrative,
            },
        )
        reports.append(
            await _store(
                session,
                pdf,
                base_dir,
                kind="repo",
                slug=f"r{repo.id}",
                title=title,
                period_kind=period_kind,
                start=start,
                end=end,
                org_ids=org_ids,
                subject_type="repo",
                subject_id=repo.id,
            )
        )
    return reports


async def _store(
    session: AsyncSession,
    pdf: bytes,
    base_dir: Path,
    *,
    kind: str,
    slug: str,
    title: str,
    period_kind: str,
    start: date,
    end: date,
    org_ids: list[int],
    subject_type: str | None = None,
    subject_id: int | None = None,
) -> Report:
    relative = _report_path(kind, slug, start)
    target = base_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pdf)
    report = Report(
        kind=kind,
        title=title,
        period_kind=period_kind,
        period_start=datetime.combine(start, time.min, tzinfo=UTC),
        period_end=datetime.combine(end, time.min, tzinfo=UTC),
        org_scope=org_ids or None,
        subject_type=subject_type,
        subject_id=subject_id,
        pdf_path=relative,
        status="generated",
    )
    session.add(report)
    await session.flush()
    return report
