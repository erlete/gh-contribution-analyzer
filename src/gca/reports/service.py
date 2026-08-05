"""Report generation service.

Three kinds. `overview` is one document over the whole scope. `person` and
`repo` are single combined documents: an introduction, a table of contents,
then one analyzed section per subject. Every narrative area is AI text when
the endpoint is configured (marked with the AI chip) and a deterministic
data statement otherwise. Documents with many sections cap the number of AI
narrative calls so generation time stays bounded; sections beyond the cap
use the fallback statements.
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

# Above this many sections, per-section narratives switch to the fallback
# statements: one AI call per section would make large documents take hours.
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
    """Generate PDF reports and persist their rows. Every kind produces one
    document; the returned list keeps the plural signature for callers."""
    if kind not in REPORT_KINDS:
        raise ValueError(f"unknown report kind: {kind}")
    base_dir = Path(reports_dir or get_settings().reports_dir)
    scope_label = await _scope_label(session, org_ids)
    label = period_label or f"{start.isoformat()} to {end.isoformat()}"
    common = {
        "period_label": label,
        "scope_label": scope_label,
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "churn_window": DEFAULT_CHURN_WINDOW_DAYS,
    }
    scope_key = ",".join(str(i) for i in sorted(org_ids)) or "all"
    period_key = f"{period_kind}:{start.isoformat()}:{end.isoformat()}"

    if kind == "overview":
        report = await _generate_overview(
            session,
            org_ids=org_ids,
            start=start,
            end=end,
            period_kind=period_kind,
            scope_key=scope_key,
            period_key=period_key,
            common=common,
            base_dir=base_dir,
            repo_ids=repo_ids,
            person_ids=person_ids,
        )
    elif kind == "person":
        report = await _generate_people_document(
            session,
            org_ids=org_ids,
            start=start,
            end=end,
            period_kind=period_kind,
            scope_key=scope_key,
            period_key=period_key,
            common=common,
            base_dir=base_dir,
            repo_ids=repo_ids,
            person_ids=person_ids,
        )
    else:
        report = await _generate_repos_document(
            session,
            org_ids=org_ids,
            start=start,
            end=end,
            period_kind=period_kind,
            scope_key=scope_key,
            period_key=period_key,
            common=common,
            base_dir=base_dir,
            repo_ids=repo_ids,
            person_ids=person_ids,
        )
    return [report]


async def _generate_overview(
    session: AsyncSession,
    *,
    org_ids: list[int],
    start: date,
    end: date,
    period_kind: str,
    scope_key: str,
    period_key: str,
    common: dict[str, object],
    base_dir: Path,
    repo_ids: list[int] | None,
    person_ids: list[int] | None,
) -> Report:
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
    title = f"Contribution overview, {common['period_label']}"
    pdf = await asyncio.to_thread(
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
    return await _store(
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


async def _generate_people_document(
    session: AsyncSession,
    *,
    org_ids: list[int],
    start: date,
    end: date,
    period_kind: str,
    scope_key: str,
    period_key: str,
    common: dict[str, object],
    base_dir: Path,
    repo_ids: list[int] | None,
    person_ids: list[int] | None,
) -> Report:
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

    use_ai = len(subjects) <= AI_SECTION_CAP
    sections: list[dict[str, object]] = []
    for me in subjects:
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
        base_dir=base_dir,
        common=common,
        org_ids=org_ids,
        start=start,
        end=end,
        period_kind=period_kind,
        scope_key=scope_key,
        period_key=period_key,
        kind="person",
        slug="people",
        title=f"Individual contributor report, {common['period_label']}",
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


async def _generate_repos_document(
    session: AsyncSession,
    *,
    org_ids: list[int],
    start: date,
    end: date,
    period_kind: str,
    scope_key: str,
    period_key: str,
    common: dict[str, object],
    base_dir: Path,
    repo_ids: list[int] | None,
    person_ids: list[int] | None,
) -> Report:
    board = await stats.repo_leaderboard(
        session, orgs=org_ids, start=start, end=end, person_ids=person_ids
    )
    subjects = [s for s in board if s.repo_id in set(repo_ids)] if repo_ids else board

    use_ai = len(subjects) <= AI_SECTION_CAP
    sections: list[dict[str, object]] = []
    for repo_stat in subjects:
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
        base_dir=base_dir,
        common=common,
        org_ids=org_ids,
        start=start,
        end=end,
        period_kind=period_kind,
        scope_key=scope_key,
        period_key=period_key,
        kind="repo",
        slug="repos",
        title=f"Repository report, {common['period_label']}",
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
    base_dir: Path,
    common: dict[str, object],
    org_ids: list[int],
    start: date,
    end: date,
    period_kind: str,
    scope_key: str,
    period_key: str,
    kind: str,
    slug: str,
    title: str,
    intro_line: str,
    sections: list[dict[str, object]],
    population: int | None,
) -> Report:
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
    pdf = await asyncio.to_thread(
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
    return await _store(
        session,
        pdf,
        base_dir,
        kind=kind,
        slug=slug,
        title=title,
        period_kind=period_kind,
        start=start,
        end=end,
        org_ids=org_ids,
    )


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
