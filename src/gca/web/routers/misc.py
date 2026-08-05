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
from gca.models import Org, Person, Repo
from gca.services import insight_context, stats
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


@router.get("/api/charts/people-performance")
async def chart_people_performance(
    request: Request, session: SessionDep, limit: int = 8
) -> JSONResponse:
    """Diverging bars: who gained and who lost the most significance
    versus the previous window of equal length."""
    scope = await get_scope(request, session)
    period = parse_range(request)
    prev_start, prev_end = insight_context.previous_range(period.start, period.end)
    board = await stats.person_leaderboard(
        session, orgs=scope.selected_ids, start=period.start, end=period.end
    )
    prev_board = await stats.person_leaderboard(
        session, orgs=scope.selected_ids, start=prev_start, end=prev_end
    )
    now = {s.person_id: s for s in board}
    before = {s.person_id: s for s in prev_board}
    deltas: list[tuple[str, float]] = []
    for pid in now.keys() | before.keys():
        name = (now.get(pid) or before[pid]).display_name
        delta = (now[pid].significance if pid in now else 0.0) - (
            before[pid].significance if pid in before else 0.0
        )
        deltas.append((name, round(delta, 1)))
    gainers = sorted((d for d in deltas if d[1] > 0), key=lambda d: d[1], reverse=True)[
        :limit
    ]
    decliners = sorted((d for d in deltas if d[1] < 0), key=lambda d: d[1])[:limit]
    ordered = [
        (name if len(name) <= 22 else name[:21] + "…", value)
        for name, value in gainers + list(reversed(decliners))
    ]
    up_color = palettes.DIVERGENT[12]
    down_color = palettes.DIVERGENT[4]
    spec = ChartSpec(
        kind="hbar",
        labels=[name for name, _ in ordered],
        series=[
            Series(
                name="Significance change",
                values=[value for _, value in ordered],
                kind="bar",
                item_colors=[
                    up_color if value > 0 else down_color for _, value in ordered
                ],
            )
        ],
        axes=["Significance change vs previous period"],
        description=(
            "People whose significance grew or shrank the most versus the "
            f"previous window, {period.label}"
        ),
    )
    return JSONResponse(render_echarts(spec))


@router.get("/partials/insight", response_class=HTMLResponse)
async def insight_partial(
    request: Request, session: SessionDep, view: str = "dashboard"
) -> Response:
    scope = await get_scope(request, session)
    period = parse_range(request)
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
        )
        area = "person"
        subject_key = f":p{person_id}"
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
        )
        area = "repo"
        subject_key = f":r{repo_id}"
    else:
        # dashboard plus the repos and people list pages: an overall status
        # of the selected window, always present, recomputed per window.
        context = await insight_context.dashboard_context(
            session,
            orgs=scope.selected_ids,
            start=period.start,
            end=period.end,
            orgs_label=scope.label,
            period_label=period.label,
        )
        area = "dashboard"
        subject_key = "" if view == "dashboard" else f":{view}"

    result = await insight_for(
        session,
        view=f"{view}{subject_key}"
        if view in ("person", "repo")
        else f"dashboard{subject_key}",
        scope_key=scope.key,
        period_key=period.key,
        context=context,
        area=area,
    )
    await session.commit()
    return templates.TemplateResponse(
        request, "_insight.html", {"text": result.text, "ai": result.ai}
    )
