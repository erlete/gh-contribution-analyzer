"""Scope cookie, chart option APIs and lazy insight partials."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gca.ai.insights import insight_for
from gca.charts import ChartSpec, Series, palettes, render_echarts
from gca.db.engine import get_session
from gca.models import Org, Person, Repo, Research
from gca.services import insight_context, research, stats
from gca.web.context import SCOPE_COOKIE, get_scope, parse_range
from gca.web.deps import templates

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/scope")
async def set_scope(
    request: Request,
    session: SessionDep,
    orgs: Annotated[list[int] | None, Query()] = None,
    next: str = "/",
) -> RedirectResponse:
    scope = await get_scope(request, session)
    valid = {org.id for org in scope.orgs}
    selected = [i for i in (orgs or []) if i in valid]
    if not selected or len(selected) == len(valid):
        value = ""
    else:
        value = ",".join(str(i) for i in selected)
    target = (
        next if next.startswith("/") and not next.startswith(("//", "/\\")) else "/"
    )
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SCOPE_COOKIE,
        value,
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite="strict",
    )
    return response


@router.get("/api/charts/trend")
async def chart_trend(request: Request, session: SessionDep) -> JSONResponse:
    """Daily commits and significance for the window, dual axis line."""
    scope = await get_scope(request, session)
    period = parse_range(request)
    repo_id = request.query_params.get("repo_id", "")
    person_id = request.query_params.get("person_id", "")
    series = await stats.timeseries(
        session,
        orgs=scope.selected_ids,
        start=period.start,
        end=period.end,
        repo_ids=[int(repo_id)] if repo_id.isdigit() else None,
        person_ids=[int(person_id)] if person_id.isdigit() else None,
    )
    spec = ChartSpec(
        kind="line",
        labels=[p.day.isoformat() for p in series],
        series=[
            Series(name="Commits", values=[float(p.commits) for p in series]),
            Series(
                name="Significance",
                values=[round(p.significance, 2) for p in series],
                axis=1,
            ),
        ],
        axes=["Commits", "Significance"],
        description=f"Daily commits and significance, {period.label}",
        zoom=len(series) > 60,
    )
    return JSONResponse(render_echarts(spec))


@router.get("/api/charts/repo-activity")
async def chart_repo_activity(
    request: Request, session: SessionDep, repo_id: int
) -> JSONResponse:
    """Stacked added and removed lines per day plus a commits line."""
    scope = await get_scope(request, session)
    period = parse_range(request)
    series = await stats.timeseries(
        session,
        orgs=scope.selected_ids,
        start=period.start,
        end=period.end,
        repo_ids=[repo_id],
    )
    spec = ChartSpec(
        kind="bar",
        labels=[p.day.isoformat() for p in series],
        series=[
            Series(
                name="Additions",
                values=[float(p.additions) for p in series],
                kind="bar",
                stack="lines",
            ),
            Series(
                name="Deletions",
                values=[float(p.deletions) for p in series],
                kind="bar",
                stack="lines",
            ),
            Series(
                name="Commits",
                values=[float(p.commits) for p in series],
                axis=1,
            ),
        ],
        axes=["Lines", "Commits"],
        description=f"Daily line changes and commits, {period.label}",
        zoom=len(series) > 60,
    )
    return JSONResponse(render_echarts(spec))


# Diverging performance charts show a consistent 2*limit bars: the top
# `limit` gainers and top `limit` decliners versus the previous window.
_PERFORMANCE_LIMIT = 10


def _performance_spec(
    entries: list[tuple[str, float]],
    *,
    limit: int,
    all_time: bool,
    subject: str,
    period_label: str,
) -> ChartSpec:
    """Build the diverging hbar for significance deltas; for all-time
    periods there is no previous window, so rank totals instead."""
    up_color = palettes.DIVERGENT[12]
    down_color = palettes.DIVERGENT[4]
    if all_time:
        ranked = sorted(
            (e for e in entries if e[1] != 0), key=lambda e: e[1], reverse=True
        )[: 2 * limit]
        ordered = ranked
        axis = "Total significance, all time"
        description = f"Most significant {subject} over the entire recorded history"
    else:
        gainers = sorted(
            (e for e in entries if e[1] > 0), key=lambda e: e[1], reverse=True
        )[:limit]
        decliners = sorted((e for e in entries if e[1] < 0), key=lambda e: e[1])[:limit]
        ordered = gainers + list(reversed(decliners))
        axis = "Significance change vs previous period"
        description = (
            f"{subject.capitalize()} whose significance grew or shrank the "
            f"most versus the previous window, {period_label}"
        )
    labels = [
        (name if len(name) <= 22 else name[:21] + "…", value) for name, value in ordered
    ]
    return ChartSpec(
        kind="hbar",
        labels=[name for name, _ in labels],
        series=[
            Series(
                name="Significance change" if not all_time else "Significance",
                values=[value for _, value in labels],
                kind="bar",
                item_colors=[
                    up_color if value > 0 else down_color for _, value in labels
                ],
            )
        ],
        axes=[axis],
        description=description,
    )


@router.get("/api/charts/people-performance")
async def chart_people_performance(
    request: Request, session: SessionDep, limit: int = _PERFORMANCE_LIMIT
) -> JSONResponse:
    """Diverging bars: who gained and who lost the most significance
    versus the previous window of equal length."""
    scope = await get_scope(request, session)
    period = parse_range(request)
    all_time = period.key == "all"
    board = await stats.person_leaderboard(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    if all_time:
        entries = [(s.display_name, round(s.significance, 1)) for s in board]
    else:
        prev_start, prev_end = insight_context.previous_range(period.start, period.end)
        prev_board = await stats.person_leaderboard(
            session, orgs=scope.selected_ids, start=prev_start, end=prev_end
        )
        now = {s.person_id: s for s in board}
        before = {s.person_id: s for s in prev_board}
        entries = []
        for pid in now.keys() | before.keys():
            name = (now.get(pid) or before[pid]).display_name
            delta = (now[pid].significance if pid in now else 0.0) - (
                before[pid].significance if pid in before else 0.0
            )
            entries.append((name, round(delta, 1)))
    spec = _performance_spec(
        entries,
        limit=limit,
        all_time=all_time,
        subject="people",
        period_label=period.label,
    )
    return JSONResponse(render_echarts(spec))


@router.get("/api/charts/repo-performance")
async def chart_repo_performance(
    request: Request, session: SessionDep, limit: int = _PERFORMANCE_LIMIT
) -> JSONResponse:
    """Diverging bars: repositories that gained and lost the most
    significance versus the previous window of equal length."""
    scope = await get_scope(request, session)
    period = parse_range(request)
    all_time = period.key == "all"
    board = await stats.repo_leaderboard(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    if all_time:
        entries = [(f"{s.org_login}/{s.name}", round(s.significance, 1)) for s in board]
    else:
        prev_start, prev_end = insight_context.previous_range(period.start, period.end)
        prev_board = await stats.repo_leaderboard(
            session, orgs=scope.selected_ids, start=prev_start, end=prev_end
        )
        now = {s.repo_id: s for s in board}
        before = {s.repo_id: s for s in prev_board}
        entries = []
        for rid in now.keys() | before.keys():
            stat = now.get(rid) or before[rid]
            name = f"{stat.org_login}/{stat.name}"
            delta = (now[rid].significance if rid in now else 0.0) - (
                before[rid].significance if rid in before else 0.0
            )
            entries.append((name, round(delta, 1)))
    spec = _performance_spec(
        entries,
        limit=limit,
        all_time=all_time,
        subject="repositories",
        period_label=period.label,
    )
    return JSONResponse(render_echarts(spec))


@router.get("/partials/insight", response_class=HTMLResponse)
async def insight_partial(
    request: Request, session: SessionDep, view: str = "dashboard"
) -> Response:
    scope = await get_scope(request, session)
    period = parse_range(request)
    all_time = period.key == "all"
    repo_id = request.query_params.get("repo_id", "")
    person_id = request.query_params.get("person_id", "")

    if view == "person" and person_id.isdigit():
        person = await session.get(Person, int(person_id))
        context = await insight_context.person_context(
            session,
            person_id=int(person_id),
            display_name=person.display_name if person else f"person {person_id}",
            orgs=scope.selected_ids,
            start=period.start,
            end=period.end,
            orgs_label=scope.label,
            period_label=period.label,
            all_time=all_time,
        )
        area = "person"
        view_name = f"person:p{person_id}"
    elif view == "research":
        # One research block: the block recomputes for the current window
        # and its context (facts plus numbers) feeds the narrative.
        research_id = request.query_params.get("research_id", "")
        block_idx = request.query_params.get("block", "")
        row = (
            await session.get(Research, int(research_id))
            if research_id.isdigit()
            else None
        )
        blocks = list(row.blocks or []) if row else []
        index = int(block_idx) if block_idx.isdigit() else -1
        if row is not None and 0 <= index < len(blocks):
            block_result = await research.run_block(
                session,
                blocks[index],
                orgs=scope.selected_ids,
                start=period.start,
                end=period.end,
                period_label=period.label,
                all_time=all_time,
            )
            context = block_result.context or {
                "period": period.label,
                "period_mode": "all time" if all_time else "window",
                "facts": [block_result.error or "Nothing to report."],
            }
        else:
            context = {
                "period": period.label,
                "period_mode": "all time" if all_time else "window",
                "facts": ["This research block no longer exists."],
            }
        area = "research"
        view_name = f"research:{research_id}:{index}"
    elif view == "repo" and repo_id.isdigit():
        repo = await session.get(Repo, int(repo_id))
        org = await session.get(Org, repo.org_id) if repo else None
        full_name = f"{org.login}/{repo.name}" if repo and org else f"repo {repo_id}"
        context = await insight_context.repo_context(
            session,
            repo_id=int(repo_id),
            full_name=full_name,
            orgs=scope.selected_ids,
            start=period.start,
            end=period.end,
            orgs_label=scope.label,
            period_label=period.label,
            all_time=all_time,
        )
        area = "repo"
        view_name = f"repo:r{repo_id}"
    else:
        # Dashboard plus the people and repos list pages: an overall status
        # of the selected window, always present, recomputed per window.
        # The list pages are group views with their own instruction areas,
        # distinct from the individual person and repo areas.
        context = await insight_context.dashboard_context(
            session,
            orgs=scope.selected_ids,
            start=period.start,
            end=period.end,
            orgs_label=scope.label,
            period_label=period.label,
            all_time=all_time,
        )
        area = view if view in ("people", "repos") else "dashboard"
        view_name = area

    result = await insight_for(
        session,
        view=view_name,
        scope_key=scope.key,
        period_key=period.key,
        context=context,
        area=area,
        kind="dashboard" if area in ("dashboard", "people", "repos") else None,
    )
    await session.commit()
    return templates.TemplateResponse(
        request, "_insight.html", {"text": result.text, "ai": result.ai}
    )
