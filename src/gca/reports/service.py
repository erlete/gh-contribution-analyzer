"""Report generation service.

Three kinds. `overview` is one document over the whole scope. `person` and
`repo` are single combined documents: an introduction, a table of contents,
then one analyzed section per subject. Every narrative area is AI text when
the endpoint is configured (marked with the AI chip) and a deterministic
data statement otherwise. Documents with many sections cap the number of AI
narrative calls so generation time stays bounded; sections beyond the cap
use the fallback statements.

Generation is split in two so it can run non-blocking: `request_report`
creates a `queued` row carrying its filters in `params`, and
`fulfill_report` renders the PDF and flips the row to `generated` (or the
caller marks it `failed`). The worker drains queued rows; the synchronous
`generate_reports` wrapper (scheduler, CLI) does both steps inline.
"""

import asyncio
from datetime import UTC, date, datetime, time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.ai.fallback import fallback_text
from gca.ai.insights import InsightResult, insight_for
from gca.config import get_settings
from gca.metrics.churn import DEFAULT_CHURN_WINDOW_DAYS
from gca.models import Org, Person, Repo, Report
from gca.reports.builder import render_pdf, trend_chart_data_uri
from gca.services import insight_context, stats
from gca.services.stats import PersonStat
from gca.timeutil import utcnow

REPORT_KINDS = ("overview", "person", "repo")

# AI narratives go to the first this-many sections (subjects are ordered by
# significance, so the ones that matter get real analysis); sections beyond
# the cap use the fallback statements so large documents stay bounded.
AI_SECTION_CAP = 40


async def _scope_label(session: AsyncSession, org_ids: list[int]) -> str:
    query = sa.select(Org.login).order_by(Org.login)
    if org_ids:
        query = query.where(Org.id.in_(org_ids))
    logins = [row.login for row in (await session.execute(query)).all()]
    return ", ".join(logins) if logins else "no orgs"


def _report_path(kind: str, slug: str, when: date) -> str:
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{when.year:04d}/{when.month:02d}/{kind}-{slug}-{stamp}.pdf"


async def _chart(session: AsyncSession, **kwargs: object) -> str | None:
    series = await stats.timeseries(session, **kwargs)  # type: ignore[arg-type]
    if not series:
        return None
    return await asyncio.to_thread(trend_chart_data_uri, series)


_TITLES = {
    "overview": "Contribution overview",
    "person": "Individual contributor report",
    "repo": "Repository report",
}

_SLUGS = {"overview": "all", "person": "people", "repo": "repos"}


async def request_report(
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
) -> Report:
    """Create a queued report row carrying its generation filters."""
    if kind not in REPORT_KINDS:
        raise ValueError(f"unknown report kind: {kind}")
    label = period_label or f"{start.isoformat()} to {end.isoformat()}"
    report = Report(
        kind=kind,
        title=f"{_TITLES[kind]}, {label}",
        period_kind=period_kind,
        period_start=datetime.combine(start, time.min, tzinfo=UTC),
        period_end=datetime.combine(end, time.min, tzinfo=UTC),
        org_scope=org_ids or None,
        params={
            "period_label": label,
            "repo_ids": repo_ids or [],
            "person_ids": person_ids or [],
        },
        status="queued",
    )
    session.add(report)
    await session.flush()
    return report


async def fulfill_report(
    session: AsyncSession,
    report: Report,
    reports_dir: str | Path | None = None,
) -> None:
    """Render a requested report and flip its row to generated. Raises on
    failure so the caller can mark the row failed."""
    base_dir = Path(reports_dir or get_settings().reports_dir)
    params = report.params or {}
    org_ids = [int(i) for i in (report.org_scope or [])]
    start = report.period_start.date()
    end = report.period_end.date()
    label = str(params.get("period_label") or f"{start} to {end}")
    repo_ids = [int(i) for i in params.get("repo_ids") or []] or None
    person_ids = [int(i) for i in params.get("person_ids") or []] or None
    common = {
        "period_label": label,
        "scope_label": await _scope_label(session, org_ids),
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "churn_window": DEFAULT_CHURN_WINDOW_DAYS,
    }
    scope_key = ",".join(str(i) for i in sorted(org_ids)) or "all"
    period_key = f"{report.period_kind}:{start.isoformat()}:{end.isoformat()}"

    if report.kind == "overview":
        pdf = await _render_overview(
            session,
            org_ids=org_ids,
            start=start,
            end=end,
            scope_key=scope_key,
            period_key=period_key,
            common=common,
            repo_ids=repo_ids,
            person_ids=person_ids,
            title=report.title,
        )
    elif report.kind == "person":
        pdf = await _render_people_document(
            session,
            org_ids=org_ids,
            start=start,
            end=end,
            scope_key=scope_key,
            period_key=period_key,
            common=common,
            repo_ids=repo_ids,
            person_ids=person_ids,
            title=report.title,
        )
    else:
        pdf = await _render_repos_document(
            session,
            org_ids=org_ids,
            start=start,
            end=end,
            scope_key=scope_key,
            period_key=period_key,
            common=common,
            repo_ids=repo_ids,
            person_ids=person_ids,
            title=report.title,
        )

    relative = _report_path(report.kind, _SLUGS[report.kind], start)
    target = base_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pdf)
    report.pdf_path = relative
    report.status = "generated"
    report.error = None
    report.generated_at = utcnow()
    await session.flush()


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
    """Synchronous request-and-fulfill path for the scheduler and the CLI.
    The returned list keeps the plural signature for callers."""
    report = await request_report(
        session,
        kind=kind,
        org_ids=org_ids,
        start=start,
        end=end,
        period_kind=period_kind,
        period_label=period_label,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    await fulfill_report(session, report, reports_dir=reports_dir)
    return [report]


async def _render_overview(
    session: AsyncSession,
    *,
    org_ids: list[int],
    start: date,
    end: date,
    scope_key: str,
    period_key: str,
    common: dict[str, object],
    repo_ids: list[int] | None,
    person_ids: list[int] | None,
    title: str,
) -> bytes:
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
    context = await insight_context.dashboard_context(
        session,
        orgs=org_ids,
        start=start,
        end=end,
        orgs_label=str(common["scope_label"]),
        period_label=str(common["period_label"]),
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    narrative = await insight_for(
        session,
        view="report:overview",
        scope_key=scope_key,
        period_key=period_key,
        context=context,
        area="report",
        kind="dashboard",
    )
    return await asyncio.to_thread(
        render_pdf,
        "overview.html",
        {
            **common,
            "title": title,
            "totals": totals,
            "people": people,
            "repos": repos,
            "chart": await _chart(
                session, orgs=org_ids, start=start, end=end, repo_ids=repo_ids
            ),
            "narrative": narrative.text,
            "narrative_ai": narrative.ai,
        },
    )


async def _section_narrative(
    session: AsyncSession,
    *,
    use_ai: bool,
    view: str,
    scope_key: str,
    period_key: str,
    context: dict[str, object],
    kind: str,
) -> InsightResult:
    if not use_ai:
        return InsightResult(text=fallback_text(kind, context), ai=False)
    return await insight_for(
        session,
        view=view,
        scope_key=scope_key,
        period_key=period_key,
        context=context,
        area="report",
        kind=kind,
    )


async def _render_people_document(
    session: AsyncSession,
    *,
    org_ids: list[int],
    start: date,
    end: date,
    scope_key: str,
    period_key: str,
    common: dict[str, object],
    repo_ids: list[int] | None,
    person_ids: list[int] | None,
    title: str,
) -> bytes:
    board = await stats.person_leaderboard(
        session, orgs=org_ids, start=start, end=end, repo_ids=repo_ids
    )
    if person_ids:
        wanted = set(person_ids)
        subjects = [s for s in board if s.person_id in wanted]
        missing = wanted - {s.person_id for s in subjects}
        for pid in sorted(missing):
            person_row = await session.get(Person, pid)
            if person_row is not None:
                subjects.append(
                    PersonStat(person_id=pid, display_name=person_row.display_name)
                )
    else:
        subjects = board

    sections: list[dict[str, object]] = []
    for index, me in enumerate(subjects):
        use_ai = index < AI_SECTION_CAP
        context = await insight_context.person_context(
            session,
            person_id=me.person_id,
            display_name=me.display_name,
            orgs=org_ids,
            start=start,
            end=end,
            orgs_label=str(common["scope_label"]),
            period_label=str(common["period_label"]),
        )
        narrative = await _section_narrative(
            session,
            use_ai=use_ai,
            view=f"report:person:{me.person_id}",
            scope_key=scope_key,
            period_key=period_key,
            context=context,
            kind="person",
        )
        split = await stats.person_repo_split(
            session, person_id=me.person_id, orgs=org_ids, start=start, end=end
        )
        sections.append(
            {
                "anchor": f"p{me.person_id}",
                "heading": me.display_name,
                "kind": "person",
                "me": me,
                "split": split,
                "metric_rows": [
                    ("commits", "Commits", f"{me.commits:,}"),
                    ("additions", "Lines added", f"{me.additions:,}"),
                    ("churn", "Churn lines", f"{me.churn:,}"),
                    ("significance", "Significance", f"{me.significance:.1f}"),
                    ("prs_merged", "PRs merged", f"{me.prs_merged:,}"),
                    ("reviews", "Reviews", f"{me.reviews:,}"),
                ],
                "chart": await _chart(
                    session,
                    orgs=org_ids,
                    start=start,
                    end=end,
                    person_ids=[me.person_id],
                ),
                "narrative": narrative.text,
                "narrative_ai": narrative.ai,
            }
        )

    return await _render_collection(
        session,
        common=common,
        org_ids=org_ids,
        start=start,
        end=end,
        scope_key=scope_key,
        period_key=period_key,
        slug="people",
        title=title,
        intro_line=(
            f"This document analyzes {len(sections)} "
            f"{'person' if len(sections) == 1 else 'people'} individually. "
            "The introduction summarizes the whole scope; each section that "
            "follows covers one person with their key numbers, percentile "
            "position, activity trend and per-repository split."
        ),
        sections=sections,
        population=len(board),
    )


async def _render_repos_document(
    session: AsyncSession,
    *,
    org_ids: list[int],
    start: date,
    end: date,
    scope_key: str,
    period_key: str,
    common: dict[str, object],
    repo_ids: list[int] | None,
    person_ids: list[int] | None,
    title: str,
) -> bytes:
    board = await stats.repo_leaderboard(
        session, orgs=org_ids, start=start, end=end, person_ids=person_ids
    )
    subjects = [s for s in board if s.repo_id in set(repo_ids)] if repo_ids else board

    sections: list[dict[str, object]] = []
    for index, repo_stat in enumerate(subjects):
        use_ai = index < AI_SECTION_CAP
        repo = await session.get_one(Repo, repo_stat.repo_id)
        full_name = f"{repo_stat.org_login}/{repo.name}"
        totals = await stats.totals(
            session, orgs=[], start=start, end=end, repo_ids=[repo.id]
        )
        contributors = await stats.repo_contributors(
            session, repo_id=repo.id, orgs=[], start=start, end=end
        )
        context = await insight_context.repo_context(
            session,
            repo_id=repo.id,
            full_name=full_name,
            orgs=org_ids,
            start=start,
            end=end,
            orgs_label=str(common["scope_label"]),
            period_label=str(common["period_label"]),
        )
        narrative = await _section_narrative(
            session,
            use_ai=use_ai,
            view=f"report:repo:{repo.id}",
            scope_key=scope_key,
            period_key=period_key,
            context=context,
            kind="repo",
        )
        sections.append(
            {
                "anchor": f"r{repo.id}",
                "heading": full_name,
                "kind": "repo",
                "totals": totals,
                "contributors": contributors,
                "chart": await _chart(
                    session, orgs=[], start=start, end=end, repo_ids=[repo.id]
                ),
                "narrative": narrative.text,
                "narrative_ai": narrative.ai,
            }
        )

    return await _render_collection(
        session,
        common=common,
        org_ids=org_ids,
        start=start,
        end=end,
        scope_key=scope_key,
        period_key=period_key,
        slug="repos",
        title=title,
        intro_line=(
            f"This document analyzes {len(sections)} "
            f"{'repository' if len(sections) == 1 else 'repositories'} "
            "individually. The introduction summarizes the whole scope; each "
            "section that follows covers one repository with its key "
            "numbers, activity trend and contributor breakdown."
        ),
        sections=sections,
        population=None,
    )


async def _render_collection(
    session: AsyncSession,
    *,
    common: dict[str, object],
    org_ids: list[int],
    start: date,
    end: date,
    scope_key: str,
    period_key: str,
    slug: str,
    title: str,
    intro_line: str,
    sections: list[dict[str, object]],
    population: int | None,
) -> bytes:
    scope_totals = await stats.totals(session, orgs=org_ids, start=start, end=end)
    intro_context = await insight_context.dashboard_context(
        session,
        orgs=org_ids,
        start=start,
        end=end,
        orgs_label=str(common["scope_label"]),
        period_label=str(common["period_label"]),
    )
    intro_narrative = await insight_for(
        session,
        view=f"report:{slug}",
        scope_key=scope_key,
        period_key=period_key,
        context=intro_context,
        area="report",
        kind="dashboard",
    )
    return await asyncio.to_thread(
        render_pdf,
        "collection.html",
        {
            **common,
            "title": title,
            "intro_line": intro_line,
            "totals": scope_totals,
            "narrative": intro_narrative.text,
            "narrative_ai": intro_narrative.ai,
            "sections": sections,
            "population": population,
        },
    )
